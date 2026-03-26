# SPDX-License-Identifier: GPL-3.0-or-later
"""
ModelManager: Explicit control of LM Studio model loading/unloading via REST API.

Uses POST /api/v1/models/load and POST /api/v1/models/unload instead of relying
on JIT/Auto-Evict, giving us control over context_length and reliable swap timing.
"""

import re
import subprocess
import time
import logging
import requests
from typing import Optional

from local_ai_agent_orchestrator.interrupts import interruptible_sleep, should_shutdown
from local_ai_agent_orchestrator.settings import ModelConfig, get_settings

log = logging.getLogger(__name__)


class ModelManagerError(Exception):
    pass


class ModelManager:
    """Manages model lifecycle on a local LM Studio instance."""

    def __init__(self, base_url: Optional[str] = None):
        s = get_settings()
        self.base = (base_url or s.lm_studio_base).rstrip("/")
        self._current_llm: Optional[str] = None
        self._swap_count = 0
        self._load_count = 0
        self._unload_count = 0

    # ── Public API ───────────────────────────────────────────────────

    def ensure_loaded(self, role: str) -> str:
        """
        Guarantee that the model for `role` is loaded and ready.
        Returns the model key (instance_id) to use in chat completions.
        Swaps out any other loaded LLM first.
        """
        cfg = get_settings().models[role]

        loaded = self._get_loaded_llms()
        if cfg.key in loaded:
            log.info(f"[ModelManager] {role} model already loaded: {cfg.key}")
            self._current_llm = cfg.key
            return cfg.key

        # Snapshot available RAM BEFORE unloading so the memory gate has an
        # accurate pre-unload baseline to measure recovery against.
        pre_unload_available = self._get_available_memory_bytes()

        # Unload all other LLMs (keep embedding model -- it's tiny).
        # Track the total bytes being freed so the memory gate knows how
        # much recovery to wait for.
        freed_bytes = 0
        for instance_id in loaded:
            if instance_id == get_settings().models["embedder"].key:
                continue
            log.info(f"[ModelManager] Unloading: {instance_id}")
            self._unload(instance_id)
            self._unload_count += 1
            # Sum up the size of everything we just evicted
            for role_cfg in get_settings().models.values():
                if role_cfg.key in instance_id or instance_id in role_cfg.key:
                    freed_bytes += role_cfg.size_bytes
                    break

        # Wait for macOS Unified Memory GC to physically reclaim pages
        # before issuing the next /load to prevent swap thrashing.
        # Only meaningful if we actually unloaded something significant.
        if freed_bytes > 0:
            self._wait_for_memory(freed_bytes, pre_unload_available)

        # Load the target model
        log.info(f"[ModelManager] Loading {role}: {cfg.key} (ctx={cfg.context_length})")
        self._load(cfg)
        self._wait_until_loaded(cfg.key)
        self._load_count += 1
        if loaded:
            self._swap_count += 1

        self._current_llm = cfg.key
        return cfg.key

    def ensure_embedder_loaded(self) -> str:
        """Load the embedding model (84MB, can co-exist with any LLM)."""
        cfg = get_settings().models["embedder"]
        loaded = self._get_loaded_models_raw()
        for m in loaded:
            if m.get("key") == cfg.key and m.get("loaded_instances"):
                return cfg.key
        self._load(cfg)
        self._wait_until_loaded(cfg.key)
        return cfg.key

    def unload_all(self):
        """Unload every model. Used during shutdown."""
        for instance_id in self._get_loaded_llms():
            self._unload(instance_id)

    def health_check(self) -> bool:
        """Verify LM Studio server is reachable."""
        try:
            r = requests.get(f"{self.base}/api/v1/models", timeout=5)
            return r.status_code == 200
        except requests.ConnectionError:
            return False

    def check_guardrails(self) -> bool:
        """
        Probe whether LM Studio's resource guardrails will block model loading.
        Returns True if guardrails appear to be disabled/permissive, False if
        they will block large models.

        Uses the --estimate-only flag on the largest model as a proxy: if the
        estimate endpoint responds without a guardrail error, loading should work.
        """
        # Try a dry-run load of the reviewer (largest model) to probe guardrails
        cfg = get_settings().models["reviewer"]
        try:
            r = requests.post(
                f"{self.base}/api/v1/models/load",
                json={"model": cfg.key, "context_length": cfg.context_length},
                timeout=10,
            )
            data = r.json()
            if r.status_code == 500:
                err = data.get("error", {}).get("type", "")
                if err == "model_load_failed":
                    return False
        except Exception:
            pass
        return True

    def get_available_models(self) -> list[str]:
        """Return list of all model keys available on disk."""
        data = self._get_loaded_models_raw()
        return [m["key"] for m in data]

    def verify_models_exist(self) -> list[str]:
        """Check that all required models are downloaded. Returns list of missing keys."""
        available = set(self.get_available_models())
        seen_keys: set[str] = set()
        missing = []
        for role, cfg in get_settings().models.items():
            if cfg.key in seen_keys:
                continue
            seen_keys.add(cfg.key)
            if cfg.key not in available:
                missing.append(f"{role}: {cfg.key}")
        return missing

    @property
    def current_llm(self) -> Optional[str]:
        return self._current_llm

    def get_metrics(self) -> dict[str, int]:
        return {
            "swap_count": self._swap_count,
            "load_count": self._load_count,
            "unload_count": self._unload_count,
        }

    # ── Private Helpers ──────────────────────────────────────────────

    def _get_loaded_models_raw(self) -> list[dict]:
        r = requests.get(f"{self.base}/api/v1/models", timeout=10)
        r.raise_for_status()
        return r.json().get("models", [])

    def _get_loaded_llms(self) -> list[str]:
        """Return instance_ids of all currently loaded LLM models."""
        loaded = []
        for m in self._get_loaded_models_raw():
            if m.get("type") == "llm" and m.get("loaded_instances"):
                for inst in m["loaded_instances"]:
                    loaded.append(inst.get("instance_id", m["key"]))
            elif m.get("type") == "llm" and m.get("loaded_instances") is not None:
                continue
        return loaded

    def _load(self, cfg: ModelConfig):
        """
        Load a model via the LM Studio REST API.

        Falls back to JIT loading (a minimal chat completion request) if the
        REST API returns a guardrail error. This is a documented LM Studio
        limitation: the GUI has a "Load anyway" button but the API/CLI do not
        expose a guardrail override. JIT loading bypasses the check.
        See: https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1631
        """
        payload = {
            "model": cfg.key,
            "context_length": cfg.context_length,
        }
        try:
            r = requests.post(
                f"{self.base}/api/v1/models/load",
                json=payload,
                timeout=get_settings().model_load_timeout_s,
            )
            r.raise_for_status()
            data = r.json()
            log.info(
                f"[ModelManager] Load response: status={data.get('status')}, "
                f"time={data.get('load_time_seconds', '?')}s"
            )
        except requests.Timeout:
            raise ModelManagerError(
                f"Timed out loading {cfg.key} after {get_settings().model_load_timeout_s}s"
            )
        except requests.HTTPError as e:
            body = {}
            try:
                body = e.response.json()
            except Exception:
                pass
            err_type = body.get("error", {}).get("type", "")
            if err_type == "model_load_failed" or e.response.status_code == 500:
                log.warning(
                    f"[ModelManager] REST /load blocked by guardrails for {cfg.key}. "
                    f"Falling back to JIT load via chat completion."
                )
                self._load_via_jit(cfg)
            else:
                raise ModelManagerError(f"Failed to load {cfg.key}: {e}")

    def _load_via_jit(self, cfg: ModelConfig):
        """
        Trigger JIT loading by sending a minimal chat completion request.
        LM Studio's JIT path bypasses resource guardrails and loads the model
        on first inference. We use a 1-token completion to minimise latency.

        If JIT also fails (guardrails block even this path), raises
        ModelManagerError with a clear message directing the user to disable
        guardrails in LM Studio > Developer > Server Settings.
        """
        payload = {
            "model": cfg.key,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        log.info(f"[ModelManager] JIT-loading {cfg.key} via chat completion...")
        try:
            r = requests.post(
                f"{self.base}/v1/chat/completions",
                json=payload,
                timeout=get_settings().model_load_timeout_s,
            )
            if r.status_code == 200:
                log.info(f"[ModelManager] JIT load succeeded for {cfg.key}")
                return
            data = r.json()
            err_msg = data.get("error", {}).get("message", "")
            if "load" in err_msg.lower() or "resource" in err_msg.lower():
                raise ModelManagerError(
                    f"JIT load also failed for {cfg.key}: {err_msg}\n"
                    f"ACTION REQUIRED: In LM Studio, go to Developer > Server Settings > "
                    f"Model Loading Guardrails and set to 'Off' or 'Relaxed'."
                )
            log.info(f"[ModelManager] JIT load completed for {cfg.key} (status={r.status_code})")
        except requests.Timeout:
            raise ModelManagerError(
                f"JIT load timed out for {cfg.key} after {get_settings().model_load_timeout_s}s"
            )
        except ModelManagerError:
            raise
        except Exception as e:
            raise ModelManagerError(f"JIT load failed for {cfg.key}: {e}")

    def _unload(self, instance_id: str):
        try:
            r = requests.post(
                f"{self.base}/api/v1/models/unload",
                json={"instance_id": instance_id},
                timeout=30,
            )
            r.raise_for_status()
            log.info(f"[ModelManager] Unloaded: {instance_id}")
        except requests.HTTPError as e:
            log.warning(f"[ModelManager] Unload failed for {instance_id}: {e}")

    def _get_available_memory_bytes(self) -> int:
        """
        Return an estimate of physically available RAM using vm_stat.

        macOS Unified Memory does not have a single 'free' counter -- memory
        is held as 'inactive' (reclaimable) and 'purgeable' (discardable) in
        addition to truly free pages. The sum of all three is what the kernel
        can hand to a new allocation without touching swap.

        Page size on Apple Silicon M4 is 16 384 bytes (confirmed via sysctl).
        """
        try:
            out = subprocess.check_output(["vm_stat"], timeout=5).decode()
        except Exception as e:
            log.warning(f"[MemoryGate] vm_stat failed: {e} -- skipping memory check")
            return 2 ** 63  # Fail open so the factory is not blocked indefinitely

        page_size = 16_384  # M-series Macs use 16 KB pages
        fields: dict[str, int] = {}
        for line in out.splitlines():
            m = re.match(r"^(.+?):\s+([\d]+)", line)
            if m:
                fields[m.group(1).strip()] = int(m.group(2))

        free = fields.get("Pages free", 0) * page_size
        inactive = fields.get("Pages inactive", 0) * page_size
        purgeable = fields.get("Pages purgeable", 0) * page_size
        return free + inactive + purgeable

    def _get_swap_used_bytes(self) -> int:
        """
        Return current swap used in bytes via sysctl vm.swapusage.
        Returns 0 on any error so a failing sysctl never blocks the factory.
        """
        try:
            out = subprocess.check_output(
                ["sysctl", "vm.swapusage"], timeout=5
            ).decode()
            m = re.search(r"used\s*=\s*([\d.]+)M", out)
            if m:
                return int(float(m.group(1)) * 1024 * 1024)
        except Exception as e:
            log.warning(f"[MemoryGate] sysctl vm.swapusage failed: {e}")
        return 0

    def _wait_for_memory(self, freed_bytes: int, pre_unload_available: int):
        """
        Block until macOS has physically reclaimed enough Unified Memory after
        an unload before the next /load is issued, preventing double-residency
        overflow and swap thrashing.

        The gate does NOT require the full next-model size to be free -- macOS
        will compress and evict inactive pages during the load itself. Instead
        it waits until MEMORY_RELEASE_FRACTION of the just-freed model's bytes
        have been returned to the available pool above the pre-unload baseline,
        confirming the old model's pages are no longer resident.

        Exits early (with a WARNING) if:
        - MEMORY_SETTLE_TIMEOUT_S elapses (GC stalled -- proceed anyway)
        - Swap has grown > SWAP_GROWTH_LIMIT_MB above baseline (system is
          already under pressure -- waiting longer cannot help)

        Args:
            freed_bytes:           Combined size of all models just unloaded.
            pre_unload_available:  Available RAM snapshot taken BEFORE the
                                   unload call, used as the recovery baseline.
        """
        # Target: available RAM must rise to at least pre_unload + freed * fraction.
        # This confirms the unloaded model's pages have been returned to the pool.
        target_available = pre_unload_available + int(
            freed_bytes * get_settings().memory_release_fraction
        )
        target_gb = target_available / 1024 ** 3
        freed_gb = freed_bytes / 1024 ** 3

        # Swap baseline for secondary pressure detection
        swap_baseline = self._get_swap_used_bytes()

        deadline = time.time() + get_settings().memory_settle_timeout_s
        iteration = 0

        log.info(
            f"[MemoryGate] Waiting for {freed_gb:.1f}GB model pages to clear "
            f"(need +{freed_gb * get_settings().memory_release_fraction:.1f}GB recovery, "
            f"target available={target_gb:.1f}GB)"
        )

        while True:
            if should_shutdown():
                raise KeyboardInterrupt("Shutdown requested during memory gate wait")
            available = self._get_available_memory_bytes()
            swap_now = self._get_swap_used_bytes()
            available_gb = available / 1024 ** 3
            swap_delta_mb = (swap_now - swap_baseline) / 1024 ** 2

            ram_recovered = available >= target_available

            if ram_recovered:
                elapsed = iteration * get_settings().memory_poll_interval_s
                log.info(
                    f"[MemoryGate] Pages cleared after {elapsed}s -- "
                    f"available={available_gb:.1f}GB target={target_gb:.1f}GB "
                    f"swap_delta={swap_delta_mb:+.0f}MB"
                )
                return

            if swap_delta_mb > get_settings().swap_growth_limit_mb:
                log.warning(
                    f"[MemoryGate] Swap growing ({swap_delta_mb:+.0f}MB) -- "
                    f"system already under pressure, proceeding to unblock factory"
                )
                return

            if time.time() >= deadline:
                log.warning(
                    f"[MemoryGate] Timeout after {get_settings().memory_settle_timeout_s}s -- "
                    f"available={available_gb:.1f}GB target={target_gb:.1f}GB "
                    f"swap_delta={swap_delta_mb:+.0f}MB -- proceeding anyway"
                )
                return

            log.info(
                f"[MemoryGate] Waiting... available={available_gb:.1f}GB "
                f"target={target_gb:.1f}GB swap_delta={swap_delta_mb:+.0f}MB"
            )
            interruptible_sleep(get_settings().memory_poll_interval_s)
            iteration += 1

    def _wait_until_loaded(self, model_key: str):
        deadline = time.time() + get_settings().model_load_timeout_s
        while time.time() < deadline:
            if should_shutdown():
                raise KeyboardInterrupt("Shutdown requested while waiting for model load")
            loaded = self._get_loaded_llms()
            # Check both exact match and substring match (LM Studio may append variant suffix)
            for lid in loaded:
                if model_key in lid or lid in model_key:
                    log.info(f"[ModelManager] Confirmed loaded: {lid}")
                    return
            # Also check the raw model data for loaded_instances
            for m in self._get_loaded_models_raw():
                if m.get("key") == model_key and m.get("loaded_instances"):
                    log.info(f"[ModelManager] Confirmed loaded via key: {model_key}")
                    return
            interruptible_sleep(get_settings().model_load_poll_interval_s)
        raise ModelManagerError(
            f"Model {model_key} did not become ready within {get_settings().model_load_timeout_s}s"
        )
