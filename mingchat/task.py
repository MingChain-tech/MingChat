"""
铭信 (MingChat) v0.3 - MingTask 任务协议
Agent间任务发布/竞标/交付/结算/仲裁

任务状态机:
PUBLISHED → BIDDING/ASSIGNED/MATCHED → EXECUTING → DELIVERED
→ ACCEPTED → SETTLED
→ REJECTED/DISPUTED → RESOLVED/ESCALATED → SETTLED
→ CANCELLED (任意阶段)
"""
import json
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from .models import (
    MsgType, TaskOp, TaskStatus, TaskFields,
    TaskPublishPayload, TaskBidPayload, TaskDeliverPayload,
    TaskSettlePayload, TaskDisputePayload,
    Message, make_task_id,
)
from .protocol import serialize_message_v0_3


class MingTask:
    """
    MingTask 任务管理器
    管理任务的状态转换和消息构建
    """

    def __init__(self):
        self._tasks: Dict[str, dict] = {}  # task_id -> task_state

    # ── 任务CRUD ───────────────────────────────────────

    def create_task(self, publish: TaskPublishPayload,
                    sender_hash160: bytes) -> dict:
        """创建任务记录（本地状态管理）"""
        task_id = make_task_id(sender_hash160, b'\x00\x00\x00')
        task = {
            "task_id": task_id,
            "status": TaskStatus.PUBLISHED,
            "publish": publish.__dict__,
            "bids": [],
            "assigned_bid": None,
            "deliveries": [],
            "disputes": [],
            "created_at": int(time.time() * 1000),
            "updated_at": int(time.time() * 1000),
        }
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def list_tasks(self, status: Optional[TaskStatus] = None,
                   task_type: Optional[str] = None) -> List[dict]:
        """按状态/类型筛选任务"""
        results = []
        for t in self._tasks.values():
            if status and t["status"] != status:
                continue
            if task_type and t["publish"].get("task_type") != task_type:
                continue
            results.append(t)
        return results

    def update_status(self, task_id: str, new_status: TaskStatus) -> bool:
        """更新任务状态（校验状态机合法性）"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if not self._is_valid_transition(task["status"], new_status):
            return False
        task["status"] = new_status
        task["updated_at"] = int(time.time() * 1000)
        return True

    # ── 任务消息构建 ───────────────────────────────────

    def build_publish_message(self, client, payload: TaskPublishPayload,
                              to_address: str) -> Message:
        """构建并发送TASK_PUBLISH消息"""
        payload_dict = payload.__dict__
        body = json.dumps(payload_dict, ensure_ascii=False).encode()
        
        # 审计字段：scope_hash = SHA256(capabilities+acceptance)[:16]
        scope_str = json.dumps({
            "caps": payload.capabilities,
            "accept": payload.acceptance_mode,
            "assign": payload.assign_mode,
        }, sort_keys=True)
        from hashlib import sha256
        
        return client.send(
            receiver_address=to_address,
            body=body,
            msg_type=MsgType.TASK_PUBLISH,
            task=TaskFields(
                task_op=TaskOp.PUBLISH,
                task_id_lo=sha256(body).digest()[:3],
            ),
        )

    def build_bid_message(self, client, payload: TaskBidPayload,
                          to_address: str) -> Message:
        """构建并发送TASK_BID消息"""
        body = json.dumps(payload.__dict__, ensure_ascii=False).encode()
        return client.send(
            receiver_address=to_address,
            body=body,
            msg_type=MsgType.TASK_BID,
            task=TaskFields(
                task_op=TaskOp.BID,
                task_id_lo=bytes.fromhex(payload.task_id[-6:]) if len(payload.task_id) >= 6 else b'\x00' * 3,
            ),
        )

    def build_deliver_message(self, client, payload: TaskDeliverPayload,
                              to_address: str) -> Message:
        """构建并发送TASK_DELIVER消息"""
        body = json.dumps(payload.__dict__, ensure_ascii=False).encode()
        return client.send(
            receiver_address=to_address,
            body=body,
            msg_type=MsgType.TASK_DELIVER,
            task=TaskFields(
                task_op=TaskOp.DELIVER,
                task_id_lo=bytes.fromhex(payload.task_id[-6:]) if len(payload.task_id) >= 6 else b'\x00' * 3,
            ),
        )

    def build_settle_message(self, client, payload: TaskSettlePayload,
                             to_address: str) -> Message:
        """构建并发送TASK_SETTLE消息"""
        body = json.dumps(payload.__dict__, ensure_ascii=False).encode()
        return client.send(
            receiver_address=to_address,
            body=body,
            msg_type=MsgType.TASK_SETTLE,
            task=TaskFields(
                task_op=TaskOp.SETTLE,
                task_id_lo=bytes.fromhex(payload.task_id[-6:]) if len(payload.task_id) >= 6 else b'\x00' * 3,
            ),
        )

    def build_dispute_message(self, client, payload: TaskDisputePayload,
                              to_address: str) -> Message:
        """构建并发送TASK_DISPUTE消息"""
        body = json.dumps(payload.__dict__, ensure_ascii=False).encode()
        return client.send(
            receiver_address=to_address,
            body=body,
            msg_type=MsgType.TASK_DISPUTE,
            task=TaskFields(
                task_op=TaskOp.ARBITRATE,
                task_id_lo=bytes.fromhex(payload.task_id[-6:]) if len(payload.task_id) >= 6 else b'\x00' * 3,
            ),
        )

    # ── 收到消息处理 ───────────────────────────────────

    def handle_task_message(self, msg: Message) -> Optional[dict]:
        """处理收到的任务相关消息，自动推进状态"""
        task_id = make_task_id(msg.sender_hash160, msg.task.task_id_lo)
        task = self._tasks.get(task_id)
        
        if msg.msg_type == MsgType.TASK_PUBLISH:
            # 解析发布消息
            try:
                data = json.loads(msg.payload)
                publish = TaskPublishPayload(**data)
                return self.create_task(publish, msg.sender_hash160)
            except (json.JSONDecodeError, TypeError) as e:
                return {"error": f"解析发布消息失败: {e}"}

        if not task:
            return {"error": f"任务 {task_id} 不存在", "task_id": task_id}

        if msg.msg_type == MsgType.TASK_BID:
            task["bids"].append(msg.get_payload_text())
            self.update_status(task_id, TaskStatus.BIDDING)

        elif msg.msg_type == MsgType.TASK_DELIVER:
            task["deliveries"].append(msg.get_payload_text())
            self.update_status(task_id, TaskStatus.DELIVERED)

        elif msg.msg_type == MsgType.TASK_SETTLE:
            self.update_status(task_id, TaskStatus.SETTLED)

        elif msg.msg_type == MsgType.TASK_DISPUTE:
            task["disputes"].append(msg.get_payload_text())
            self.update_status(task_id, TaskStatus.DISPUTED)

        return task

    # ── 状态机校验 ───────────────────────────────────

    def _is_valid_transition(self, current: TaskStatus, target: TaskStatus) -> bool:
        """校验状态转换合法性"""
        transitions = {
            TaskStatus.PUBLISHED: [
                TaskStatus.BIDDING, TaskStatus.ASSIGNED,
                TaskStatus.MATCHED, TaskStatus.CANCELLED,
            ],
            TaskStatus.BIDDING: [
                TaskStatus.ASSIGNED, TaskStatus.MATCHED,
                TaskStatus.CANCELLED,
            ],
            TaskStatus.ASSIGNED: [
                TaskStatus.EXECUTING, TaskStatus.CANCELLED,
            ],
            TaskStatus.MATCHED: [
                TaskStatus.EXECUTING, TaskStatus.CANCELLED,
            ],
            TaskStatus.EXECUTING: [
                TaskStatus.DELIVERED, TaskStatus.CANCELLED,
            ],
            TaskStatus.DELIVERED: [
                TaskStatus.ACCEPTED, TaskStatus.REJECTED,
                TaskStatus.DISPUTED,
            ],
            TaskStatus.ACCEPTED: [TaskStatus.SETTLED],
            TaskStatus.REJECTED: [TaskStatus.SETTLED, TaskStatus.DISPUTED],
            TaskStatus.DISPUTED: [
                TaskStatus.RESOLVED, TaskStatus.ESCALATED,
            ],
            TaskStatus.RESOLVED: [TaskStatus.SETTLED],
            TaskStatus.ESCALATED: [TaskStatus.RESOLVED],
        }
        allowed = transitions.get(current, [])
        return target in allowed


# ── 快捷工厂函数 ───────────────────────────────────────

def make_publish_payload(
    task_type: str = "analysis",
    title: str = "",
    reward_sats: int = 0,
    deadline: int = 0,
    capabilities: Optional[List[str]] = None,
    assign_mode: str = "bid",
) -> TaskPublishPayload:
    """快速创建任务发布载荷"""
    return TaskPublishPayload(
        task_id="",
        task_type=task_type,
        title=title,
        reward_sats=reward_sats,
        deadline=deadline,
        capabilities=capabilities or [],
        assign_mode=assign_mode,
    )


def make_bid_payload(
    task_id: str,
    bid_sats: int = 0,
    estimated_time: int = 3600,
) -> TaskBidPayload:
    """快速创建竞标载荷"""
    return TaskBidPayload(
        task_id=task_id,
        bid_sats=bid_sats,
        estimated_time=estimated_time,
    )


def make_deliver_payload(
    task_id: str,
    result_hash: str = "",
    summary: str = "",
) -> TaskDeliverPayload:
    """快速创建交付载荷"""
    return TaskDeliverPayload(
        task_id=task_id,
        result_hash=result_hash,
        summary=summary,
    )


def make_settle_payload(
    task_id: str,
    verdict: str = "accepted",
    amount_sats: int = 0,
) -> TaskSettlePayload:
    """快速创建结算载荷"""
    return TaskSettlePayload(
        task_id=task_id,
        verdict=verdict,
        amount_sats=amount_sats,
    )
