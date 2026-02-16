"""Base channel interface for chat platforms."""

import time
from abc import ABC, abstractmethod
from typing import Any

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.
    """

    name: str = "base"
    max_message_chars: int | None = None

    def __init__(self, config: Any, bus: MessageBus):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
        """
        self.config = config
        self.bus = bus
        self._running = False
        self._rate_limit_s = max(int(getattr(config, "rate_limit_s", 0) or 0), 0)
        self._last_seen: dict[str, float] = {}

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.
        """
        pass

    def _split_content(self, content: str) -> list[str]:
        """Split content into chunks suitable for the channel."""
        # Some upstream paths can legitimately produce an empty/whitespace-only response
        # (e.g. tool-only turns, failed deliveries, etc.). Most chat APIs reject those.
        if content is None:
            return []
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            return []

        max_len = self.max_message_chars or 0
        if max_len <= 0 or len(content) <= max_len:
            return [content]

        chunks: list[str] = []
        start = 0
        while start < len(content):
            end = min(start + max_len, len(content))
            if end < len(content):
                split = content.rfind("\n", start, end)
                if split != -1 and split > start + int(max_len * 0.5):
                    end = split + 1
            chunks.append(content[start:end])
            start = end
        return chunks

    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.

        Args:
            sender_id: The sender's identifier.

        Returns:
            True if allowed, False otherwise.
        """
        allow_list = getattr(self.config, "allow_from", [])

        # If no allow list, allow everyone
        if not allow_list:
            return True

        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
        """
        if not self.is_allowed(sender_id):
            return

        if self._rate_limit_s > 0:
            now = time.monotonic()
            rate_key = f"{sender_id}:{chat_id}"
            last_seen = self._last_seen.get(rate_key)
            if last_seen is not None and (now - last_seen) < self._rate_limit_s:
                return
            self._last_seen[rate_key] = now

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
        )

        await self.bus.publish_inbound(msg)

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
