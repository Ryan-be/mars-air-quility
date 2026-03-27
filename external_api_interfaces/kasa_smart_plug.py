import logging

from kasa import SmartPlug

log = logging.getLogger(__name__)


class KasaSmartPlug:
    """
    A class to interact with a Kasa Smart Plug.

    Attributes:
        ip_address (str): The IP address of the smart plug.
        plug (SmartPlug): An instance of the SmartPlug class from the Kasa library.
    """

    def __init__(self, ip_address: str):
        """
        Initializes the KasaSmartPlug instance.

        Args:
            ip_address (str): The IP address of the smart plug.
        """
        self.plug = SmartPlug(ip_address)

    async def switch(self, state: bool):
        """
        Switches the smart plug on or off based on the provided state.

        Args:
            state (bool): True to turn the plug on, False to turn it off.

        Raises:
            Exception: If the operation fails.
        """
        try:
            await self.plug.update()
            if state:
                await self.plug.turn_on()
            else:
                await self.plug.turn_off()
        except Exception as e:
            log.error("Failed to switch plug state: %s", e)

    async def get_power(self):
        """
        Returns current power consumption in watts and today's usage in kWh.
        Returns None values if the plug does not have an energy meter.

        Handles python-kasa API changes across versions:
        - ≤ 0.6: modules["Emeter"], .current_consumption / .consumption_today
        - ≥ 0.7: modules["IotEmeter"], same attrs; also exposes emeter_realtime

        Note: callers (log_data / get_fan_state) already call plug.update()
        before dispatching this coroutine, so we skip a redundant update here
        to avoid a second round-trip and potential concurrency issues.
        """
        log.info("get_power called — has_emeter=%s modules=%s",
                 self.plug.has_emeter, list(self.plug.modules.keys()))

        if not self.plug.has_emeter:
            # Some plugs need a fresh update to populate emeter; try once.
            try:
                await self.plug.update()
                log.info("After update — has_emeter=%s modules=%s",
                         self.plug.has_emeter, list(self.plug.modules.keys()))
            except Exception as exc:
                log.error("update() inside get_power failed: %s", exc)
                return {"power_w": None, "today_kwh": None}

        if not self.plug.has_emeter:
            log.warning("Plug reports no emeter after update — power unavailable")
            return {"power_w": None, "today_kwh": None}

        # ── Strategy 1: module-based access (key name changed in kasa ≥ 0.7) ──
        for key in ("IotEmeter", "Emeter"):
            emeter = self.plug.modules.get(key)
            if emeter is None:
                continue
            power_w   = (getattr(emeter, "current_consumption", None)
                         or getattr(emeter, "current_power_w", None)
                         or getattr(emeter, "power", None))
            today_kwh = (getattr(emeter, "consumption_today", None)
                         or getattr(emeter, "consumption_today_kwh", None)
                         or getattr(emeter, "today_kwh", None))
            log.info("Power via module '%s': %s W, %s kWh", key, power_w, today_kwh)
            return {"power_w": power_w, "today_kwh": today_kwh}

        # ── Strategy 2: high-level emeter_realtime property (kasa ≥ 0.7) ──
        try:
            realtime  = self.plug.emeter_realtime          # EmeterStatus dict
            power_w   = realtime.get("power") or realtime.get("power_mw", 0) / 1000
            today_kwh = getattr(self.plug, "emeter_today", None)
            log.info("Power via emeter_realtime: %s W, %s kWh", power_w, today_kwh)
            return {"power_w": power_w, "today_kwh": today_kwh}
        except Exception as exc:
            log.error("emeter_realtime fallback failed: %s", exc)

        log.error("All strategies exhausted — power data unavailable")
        return {"power_w": None, "today_kwh": None}

    async def get_state(self):
        """
        Returns the state of the smart plug for serialization.

        Returns:
            dict: The state of the smart plug.
        """
        await self.plug.update()  # Ensure the plug state is updated
        return {
            'ip_address': self.plug.host,
            'state': self.plug.is_on
        }
# Example usage:
# async def main():
#     smart_plug = KasaSmartPlug("192.168.1.63")
#     is_healthy = await smart_plug.health()
#     print(f"Plug health: {is_healthy}")
#     await smart_plug.switch(True)  # Turn on the plug
#     await smart_plug.switch(False)  # Turn off the plug
#
# if __name__ == "__main__":
#     asyncio.run(main())
