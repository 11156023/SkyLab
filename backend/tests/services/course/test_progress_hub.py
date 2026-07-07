"""CourseProgressHub 單元測試（假 WebSocket，不需 DB）。"""

import asyncio
import uuid

from app.services.course.progress_hub import CourseProgressHub


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self._closed = asyncio.Event()

    async def receive_text(self) -> str:
        await self._closed.wait()
        raise ConnectionError("closed")

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def close(self):
        self._closed.set()


def test_broadcast_reaches_only_subscribers_of_that_path():
    async def scenario():
        hub = CourseProgressHub()
        path_a, path_b = uuid.uuid4(), uuid.uuid4()
        sock_a, sock_b = FakeSocket(), FakeSocket()

        task_a = asyncio.create_task(hub.register(path_id=path_a, websocket=sock_a))
        task_b = asyncio.create_task(hub.register(path_id=path_b, websocket=sock_b))
        await asyncio.sleep(0)  # 讓 register 進入等待

        await hub.broadcast(path_a, {"type": "progress", "value": 1})
        assert sock_a.sent == [{"type": "progress", "value": 1}]
        assert sock_b.sent == []

        sock_a.close()
        sock_b.close()
        await asyncio.gather(task_a, task_b)

    asyncio.run(scenario())


def test_disconnected_socket_is_removed():
    async def scenario():
        hub = CourseProgressHub()
        path_id = uuid.uuid4()
        sock = FakeSocket()
        task = asyncio.create_task(hub.register(path_id=path_id, websocket=sock))
        await asyncio.sleep(0)
        assert hub.subscriber_count(path_id) == 1

        sock.close()
        await task
        assert hub.subscriber_count(path_id) == 0

        # 對無訂閱者的 path 廣播不應噴錯
        await hub.broadcast(path_id, {"type": "progress"})

    asyncio.run(scenario())


def test_send_failure_evicts_dead_connection():
    async def scenario():
        hub = CourseProgressHub()
        path_id = uuid.uuid4()

        class DeadSocket(FakeSocket):
            async def send_json(self, data: dict) -> None:
                raise ConnectionError("dead")

        sock = DeadSocket()
        task = asyncio.create_task(hub.register(path_id=path_id, websocket=sock))
        await asyncio.sleep(0)

        await hub.broadcast(path_id, {"type": "progress"})
        assert hub.subscriber_count(path_id) == 0

        sock.close()
        await task

    asyncio.run(scenario())
