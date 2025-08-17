import asyncio
from kasa import SmartPlug


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
            print(f"Failed to switch plug state: {e}")

    def get_state(self):
        """
        Returns the state of the smart plug for serialization.

        Returns:
            dict: The state of the smart plug.
        """
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
