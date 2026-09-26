"""保存每个业务办到哪一步，并处理暂停、恢复、完成和业务切换。"""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from app.engine.constants import (
    ACTION_SUCCESS_STATUS,
    ActionResultField,
    ChatField,
    CompletedActionField,
    FlowField,
    FlowStatus,
)
from app.engine.loader import PluginLoader, plugin_loader
from app.engine.outputs import DialogueOutput
from app.engine.route_tasks import (
    ROUTE_TASK_REFUND,
    ROUTE_TASK_REFUND_UNSUBSCRIBE,
    ROUTE_TASK_UNSUBSCRIBE,
)
from app.engine.state import ChatState


class FlowManager:
    """管理一轮请求里的业务进度，最后仍按原来的字典格式返回。"""

    def __init__(self, state: ChatState, loader: PluginLoader = plugin_loader) -> None:
        self.loader = loader
        raw_flows = state.get(ChatField.FLOWS)
        # 在副本上处理，避免本轮失败时顺手改掉传进来的会话数据。
        self.flows: list[dict[str, Any]] = (
            deepcopy(raw_flows) if isinstance(raw_flows, list) else []
        )
        self.active_flow_id: str | None = state.get(ChatField.ACTIVE_FLOW_ID)
        active = self.active()
        if active is not None:
            self.active_flow_id = str(active[FlowField.FLOW_ID])
            return

        # 兼容只有 business/plugin_state/context 的旧会话。
        business = state.get(ChatField.BUSINESS)
        if business and business in self.loader.plugins:
            plugin = self.loader.get(str(business))
            if not plugin.is_overlay:
                flow = self._new_flow(
                    plugin.name,
                    plugin_state=state.get(ChatField.PLUGIN_STATE) or plugin.initial_state,
                    context=state.get(ChatField.CONTEXT, {}),
                    unrecognized_count=state.get(ChatField.UNRECOGNIZED_COUNT, 0),
                )
                self.flows.append(flow)
                self.active_flow_id = str(flow[FlowField.FLOW_ID])

    def active(self) -> dict[str, Any] | None:
        """先找指定的活动流程；找不到时尝试使用列表里最近的活动流程。"""

        if self.active_flow_id:
            matched = next(
                (
                    flow
                    for flow in self.flows
                    if flow.get(FlowField.FLOW_ID) == self.active_flow_id
                    and flow.get(FlowField.STATUS) == FlowStatus.ACTIVE
                ),
                None,
            )
            if matched is not None:
                return matched
        return next(
            (
                flow
                for flow in reversed(self.flows)
                if flow.get(FlowField.STATUS) == FlowStatus.ACTIVE
            ),
            None,
        )

    def activate(
        self,
        business: str,
        *,
        context: dict[str, Any] | None = None,
        completed_actions: list[dict[str, Any]] | None = None,
    ) -> str:
        """优先接着办之前暂停的同类业务，没有旧流程再新建。"""

        suspended = next(
            (
                flow
                for flow in reversed(self.flows)
                if flow.get(FlowField.BUSINESS) == business
                and flow.get(FlowField.STATUS) == FlowStatus.SUSPENDED
            ),
            None,
        )
        if suspended is not None:
            suspended[FlowField.STATUS] = FlowStatus.ACTIVE
            if context:
                # 旧流程已经确认过的信息优先，避免切回时被新流程覆盖。
                suspended[FlowField.CONTEXT] = {**context, **suspended.get(FlowField.CONTEXT, {})}
            if completed_actions:
                suspended[FlowField.COMPLETED_ACTIONS] = self._merge_completed_actions(
                    completed_actions,
                    suspended.get(FlowField.COMPLETED_ACTIONS, []),
                )
            self.active_flow_id = str(suspended[FlowField.FLOW_ID])
            return self.active_flow_id
        flow = self._new_flow(
            business,
            context=context,
            completed_actions=completed_actions,
        )
        self.flows.append(flow)
        self.active_flow_id = str(flow[FlowField.FLOW_ID])
        return self.active_flow_id

    def complete(self, flow: dict[str, Any]) -> None:
        flow[FlowField.STATUS] = FlowStatus.COMPLETED
        self.active_flow_id = None

    def switch(self, source: dict[str, Any], target_business: str) -> str:
        """普通切换先暂停原业务；切到组合业务时带上已查到和已办成的信息。"""

        source_task = self.loader.get(str(source[FlowField.BUSINESS])).route_task
        target_task = self.loader.get(target_business).route_task
        merge = (
            target_task == ROUTE_TASK_REFUND_UNSUBSCRIBE
            and source_task in {ROUTE_TASK_REFUND, ROUTE_TASK_UNSUBSCRIBE}
        )
        # 退款或退订升级成“退款并退订”后，原流程由组合流程接替。
        source[FlowField.STATUS] = FlowStatus.SUPERSEDED if merge else FlowStatus.SUSPENDED
        return self.activate(
            target_business,
            context=source.get(FlowField.CONTEXT, {}) if merge else None,
            completed_actions=source.get(FlowField.COMPLETED_ACTIONS, []) if merge else None,
        )

    def commit(self, result: ChatState) -> ChatState:
        """把本轮处理后的状态写回当前流程，并记录已经办成的动作。"""

        flow = self.active()
        if flow is None:
            raise RuntimeError("提交结果时 ACTIVE 流程不存在")
        flow[FlowField.PLUGIN_STATE] = result.get(ChatField.PLUGIN_STATE)
        flow[FlowField.CONTEXT] = deepcopy(result.get(ChatField.CONTEXT, {}))
        flow[FlowField.UNRECOGNIZED_COUNT] = int(result.get(ChatField.UNRECOGNIZED_COUNT, 0))

        output = result.get(ChatField.OUT, DialogueOutput.CHAT)
        action_result = result.get(ChatField.ACTION_RESULT, {})
        # 只有动作明确成功且对外结果是业务办理，才记为已完成。
        if action_result.get(ActionResultField.OK) and output in {
            DialogueOutput.REFUND,
            DialogueOutput.UNSUBSCRIBE,
            DialogueOutput.REFUND_UNSUBSCRIBE,
        }:
            completed = {
                CompletedActionField.ACTION: str(
                    result.get(ChatField.ACTION) or output.value.lower()
                ),
                CompletedActionField.OUT: output.value,
                CompletedActionField.ORDER_NO: action_result.get(CompletedActionField.ORDER_NO),
                CompletedActionField.STATUS: ACTION_SUCCESS_STATUS,
            }
            flow[FlowField.COMPLETED_ACTIONS] = self._merge_completed_actions(
                flow.get(FlowField.COMPLETED_ACTIONS, []), [completed]
            )

        if output in {DialogueOutput.HUMAN, DialogueOutput.END}:
            # 转人工或结束后不再锁定当前业务，下一轮可以重新路由。
            self.complete(flow)

        return {
            **result,
            ChatField.HANDLED_BY: str(result[ChatField.BUSINESS]),
            ChatField.FLOWS: self.flows,
            ChatField.ACTIVE_FLOW_ID: self.active_flow_id,
            ChatField.PENDING_SWITCH: None,
        }

    def _new_flow(
        self,
        business: str,
        *,
        plugin_state: str | None = None,
        context: dict[str, Any] | None = None,
        unrecognized_count: int = 0,
        completed_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        plugin = self.loader.get(business)
        return {
            FlowField.FLOW_ID: f"flow_{uuid4().hex}",
            FlowField.BUSINESS: business,
            FlowField.PLUGIN_STATE: plugin_state or plugin.initial_state,
            FlowField.STATUS: FlowStatus.ACTIVE,
            FlowField.CONTEXT: deepcopy(context or {}),
            FlowField.UNRECOGNIZED_COUNT: max(0, int(unrecognized_count)),
            FlowField.COMPLETED_ACTIONS: deepcopy(completed_actions or []),
        }

    @staticmethod
    def _merge_completed_actions(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并办过的动作，按动作、结果和订单号去重。"""

        merged: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any, Any]] = set()
        for group in groups:
            for item in group:
                key = (
                    item.get(CompletedActionField.ACTION),
                    item.get(CompletedActionField.OUT),
                    item.get(CompletedActionField.ORDER_NO),
                )
                if key not in seen:
                    seen.add(key)
                    merged.append(deepcopy(item))
        return merged
