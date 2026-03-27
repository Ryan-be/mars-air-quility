import logging
from datetime import datetime

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

        Always calls update() first so properties are populated regardless
        of whether the caller has already done so.
        """
        try:
            await self.plug.update()
        except Exception as exc:
            log.error("update() in get_power failed: %s", exc)
            return {"power_w": None, "today_kwh": None}

        log.info("get_power — has_emeter=%s modules=%s",
                 self.plug.has_emeter, list(self.plug.modules.keys()))

        # ── Strategy 1: real-time emeter (KP115, EP25, KP125 …) ─────────────
        if self.plug.has_emeter:
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

            # High-level emeter_realtime fallback (kasa ≥ 0.7)
            try:
                realtime  = self.plug.emeter_realtime
                power_w   = realtime.get("power") or realtime.get("power_mw", 0) / 1000
                today_kwh = getattr(self.plug, "emeter_today", None)
                log.info("Power via emeter_realtime: %s W, %s kWh", power_w, today_kwh)
                return {"power_w": power_w, "today_kwh": today_kwh}
            except Exception as exc:
                log.error("emeter_realtime fallback failed: %s", exc)

        # ── Strategy 2: usage module (HS100/HS103/EP10 — no real-time watts) ─
        # These plugs track daily kWh but cannot report live power draw.
        usage = self.plug.modules.get("usage")
        if usage is not None:
            now = datetime.now()
            try:
                # kasa ≥ 0.7 uses keyword-only args; try both calling conventions
                try:
                    stats = await usage.get_daystat(year=now.year, month=now.month)
                except TypeError:
                    stats = await usage.get_daystat(now.year, now.month)
                log.info("usage.get_daystat raw response: %s", stats)
                today_kwh = None
                day_list = (
                    stats.get("day_list", []) if isinstance(stats, dict)
                    else getattr(stats, "day_list", [])
                )
                for entry in day_list:
                    entry_day = (
                        entry.get("day") if isinstance(entry, dict)
                        else getattr(entry, "day", None)
                    )
                    energy = (
                        entry.get("energy_wh", 0) if isinstance(entry, dict)
                        else getattr(entry, "energy_wh", 0)
                    )
                    if entry_day == now.day:
                        today_kwh = round(energy / 1000, 4)
                        break
                log.info("Usage module: today_kwh=%s (no real-time watts on this model)",
                         today_kwh)
                return {"power_w": None, "today_kwh": today_kwh}
            except Exception as exc:
                log.error("usage module fallback failed: %s", exc)

        log.warning("No energy data available for this plug model "
                    "(no emeter, no usage module, or usage call failed)")
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
