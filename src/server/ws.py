"""
asyncio WebSocket server with typed message protocol.
Pushes AgentNodeOutput to connected clients on each pipeline node completion.
"""

import json
import logging
from websockets.asyncio.server import serve as ws_serve
from src.models.output import WSMessage, WSMessageType, AgentNodeOutput, now_iso

logger = logging.getLogger(__name__)

# 已连接客户端；依靠 asyncio 单线程语义保护，只在 await 间隙修改
_connected: set = set()
# 供较晚连接客户端恢复的完整流程状态
_full_state: list[AgentNodeOutput] = []


def _make_message(msg_type: WSMessageType, payload: object) -> str:
    msg = WSMessage(type=msg_type, timestamp=now_iso(), payload=payload)
    return msg.model_dump_json()


async def _handler(websocket):
    """Handle a single WebSocket connection."""
    _connected.add(websocket)
    logger.info("WS client connected (%d total)", len(_connected))

    # 向较晚连接的客户端发送完整状态
    if _full_state:
        await websocket.send(
            _make_message(WSMessageType.FULL_STATE,
                          [s.model_dump() for s in _full_state])
        )

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                if data.get("type") == "heartbeat":
                    await websocket.send(_make_message(WSMessageType.HEARTBEAT, {}))
            except json.JSONDecodeError:
                logger.debug("WS: received non-JSON message, ignoring")
    except Exception:
        logger.debug("WS: client read loop ended", exc_info=True)
    finally:
        _connected.discard(websocket)
        logger.info("WS client disconnected (%d total)", len(_connected))


async def _broadcast(message: str):
    """Send a message to all connected clients, pruning dead connections."""
    global _connected
    dead: set = set()
    for ws in _connected:
        try:
            await ws.send(message)
        except Exception:
            dead.add(ws)
            logger.debug("WS: pruned dead connection")
    _connected -= dead


async def broadcast_node_update(node_output: AgentNodeOutput):
    """Push a single node update to all connected clients."""
    node_key = node_output.node_id or node_output.role
    for idx, existing in enumerate(_full_state):
        if (existing.node_id or existing.role) == node_key:
            _full_state[idx] = node_output
            break
    else:
        _full_state.append(node_output)
    message = _make_message(WSMessageType.NODE_UPDATE, node_output.model_dump())
    await _broadcast(message)


async def broadcast_pipeline_complete(outputs: list[AgentNodeOutput]):
    """Push pipeline completion with all outputs."""
    message = _make_message(WSMessageType.PIPELINE_COMPLETE,
                            [o.model_dump() for o in outputs])
    await _broadcast(message)


async def broadcast_error(error_msg: str, node_id: str = ""):
    """Push an error to all connected clients."""
    message = _make_message(WSMessageType.ERROR,
                            {"message": error_msg, "node_id": node_id})
    await _broadcast(message)


def reset_state():
    """Reset full state for a new pipeline run."""
    _full_state.clear()


async def start_server(host: str = "localhost", port: int = 8765):
    """Start the WebSocket server. Returns the server object."""
    logger.info("WS server starting on %s:%s", host, port)
    server = await ws_serve(_handler, host, port)
    return server
