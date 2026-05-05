"""Actuator abstract base class.

An Actuator is a switchable load. .pulse() turns it on for a duration then
off, with a safety cap to prevent runaway commands. The safety_max_pulse_s
defaults to 30s per the spec; constructor parameter lets tests use shorter.
"""
from abc import ABC, abstractmethod


class Actuator(ABC):
    @abstractmethod
    def on(self) -> None: ...

    @abstractmethod
    def off(self) -> None: ...

    @abstractmethod
    def state(self) -> bool: ...

    @abstractmethod
    def pulse(self, seconds: float) -> None: ...
