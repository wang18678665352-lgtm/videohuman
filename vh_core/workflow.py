"""工作流:用 JSON 描述"节点 + 连线",在同一个执行器(Pipeline)上组合任意步骤。

格式(与工作台可视化编辑器的数据结构一致):
    {
      "id": "broll_voiceover", "title": "配音混剪", "description": "...",
      "nodes": [{"id": "script", "type": "input_text", "params": {"text": ""}, "position": {"x": 0, "y": 0}}, ...],
      "edges": [{"source": "script", "sourceHandle": "text", "target": "tts", "targetHandle": "text"}, ...],
      "final": "post.final"     # 可选:成品产物(节点.输出端口),缺省取最后一个视频输出
    }

内置模板在 vh_core/workflows/*.json(只读);用户工作流存 settings.workflows(默认 configs/workflows/)。
任务运行时把工作流快照写入 workspace/{job_id}/workflow.json,之后修改模板不影响该任务续跑。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .pipeline import Node, Pipeline, atomic_write_text, expand_dotted, kinds_compatible
from .stages import STEP_TYPES

BUILTIN_DIR = Path(__file__).parent / "workflows"
JOB_SNAPSHOT = "workflow.json"

_WORKFLOW_ID = re.compile(r"[a-z0-9][a-z0-9_\-]{0,63}")
# 节点 id 用作 job 目录下的子目录名
_NODE_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")


class NodeSpec(BaseModel):
    id: str
    type: str
    title: str | None = None  # 画布上显示的名字,缺省用步骤名
    params: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] | None = None


class EdgeSpec(BaseModel):
    source: str
    sourceHandle: str
    target: str
    targetHandle: str


class WorkflowSpec(BaseModel):
    id: str
    title: str
    description: str = ""
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)
    final: str | None = None


class WorkflowError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("工作流无效:" + ";".join(errors))
        self.errors = errors


# ---------------------------------------------------------------- 步骤元数据


def step_catalog() -> list[dict[str, Any]]:
    """全部步骤类型的端口/参数描述,供工作台节点面板与参数表单使用。"""
    out = []
    for name, cls in STEP_TYPES.items():
        out.append(
            {
                "type": name,
                "title": cls.title or name,
                "category": cls.category,
                "description": cls.description,
                "inputs": [{"name": k, **asdict(p)} for k, p in cls.inputs.items()],
                "outputs": [{"name": k, **asdict(p)} for k, p in cls.outputs.items()],
                "params": [{**asdict(p), "options": list(p.options)} for p in cls.params],
            }
        )
    return out


# ---------------------------------------------------------------- 校验与排序


def topo_order(spec: WorkflowSpec) -> list[NodeSpec]:
    """拓扑序(同层按节点列表顺序,结果稳定)。有环时抛 WorkflowError。"""
    ids = [n.id for n in spec.nodes]
    indeg = dict.fromkeys(ids, 0)
    succ: dict[str, list[str]] = {i: [] for i in ids}
    for e in spec.edges:
        if e.source in indeg and e.target in indeg:
            indeg[e.target] += 1
            succ[e.source].append(e.target)
    by_id = {n.id: n for n in spec.nodes}
    ready = [i for i in ids if indeg[i] == 0]
    order: list[NodeSpec] = []
    while ready:
        cur = ready.pop(0)
        order.append(by_id[cur])
        for nxt in succ[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=ids.index)
    if len(order) != len(ids):
        raise WorkflowError(["连线存在环路"])
    return order


def validate_workflow(spec: WorkflowSpec) -> list[str]:
    errors: list[str] = []
    if not _WORKFLOW_ID.fullmatch(spec.id):
        errors.append(f"工作流 id 非法: {spec.id!r}(小写字母数字、下划线、连字符)")
    if not spec.nodes:
        errors.append("工作流没有节点")
    nodes: dict[str, NodeSpec] = {}
    for n in spec.nodes:
        if not _NODE_ID.fullmatch(n.id):
            errors.append(f"节点 id 非法: {n.id!r}(小写字母开头,字母数字下划线)")
        if n.id in nodes:
            errors.append(f"节点 id 重复: {n.id}")
        if n.type not in STEP_TYPES:
            errors.append(f"节点 {n.id} 的步骤类型未知: {n.type}")
        nodes[n.id] = n

    connected: set[tuple[str, str]] = set()
    for e in spec.edges:
        src, dst = nodes.get(e.source), nodes.get(e.target)
        if src is None or dst is None:
            errors.append(f"连线引用了不存在的节点: {e.source} → {e.target}")
            continue
        if src.type not in STEP_TYPES or dst.type not in STEP_TYPES:
            continue
        out_port = STEP_TYPES[src.type].outputs.get(e.sourceHandle)
        in_port = STEP_TYPES[dst.type].inputs.get(e.targetHandle)
        if out_port is None:
            errors.append(f"节点 {src.id} 没有输出 '{e.sourceHandle}'")
            continue
        if in_port is None:
            errors.append(f"节点 {dst.id} 没有输入 '{e.targetHandle}'")
            continue
        if not kinds_compatible(out_port.kind, in_port.kind):
            errors.append(
                f"类型不匹配: {src.id}.{e.sourceHandle}({out_port.kind}) → {dst.id}.{e.targetHandle}({in_port.kind})"
            )
        if (dst.id, e.targetHandle) in connected:
            errors.append(f"节点 {dst.id} 的输入 '{e.targetHandle}' 只能连一条线")
        connected.add((dst.id, e.targetHandle))

    for n in spec.nodes:
        cls = STEP_TYPES.get(n.type)
        if cls is None:
            continue
        for port_name, port in cls.inputs.items():
            if port.required and (n.id, port_name) not in connected:
                errors.append(f"节点 {n.id}({cls.title}) 的必需输入 '{port.title or port_name}' 未连线")

    if spec.final:
        node_id, _, port = spec.final.partition(".")
        n = nodes.get(node_id)
        if n is None or n.type not in STEP_TYPES or port not in STEP_TYPES[n.type].outputs:
            errors.append(f"成品产物 final 引用无效: {spec.final}")
    if not errors:
        try:
            topo_order(spec)
        except WorkflowError as e:
            errors.extend(e.errors)
    return errors


def ensure_valid(spec: WorkflowSpec) -> None:
    errors = validate_workflow(spec)
    if errors:
        raise WorkflowError(errors)


def final_ref(spec: WorkflowSpec) -> str | None:
    """成品产物引用:显式 final,否则取拓扑序里最后一个视频输出。"""
    if spec.final:
        return spec.final
    for n in reversed(topo_order(spec)):
        for port_name, port in STEP_TYPES[n.type].outputs.items():
            if port.kind == "video":
                return f"{n.id}.{port_name}"
    return None


def effective_params(spec: WorkflowSpec, node_params: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """节点参数 = 工作流里的默认值 + 本次任务的覆盖值(仍为点号扁平形式)。"""
    node_params = node_params or {}
    return {n.id: {**n.params, **(node_params.get(n.id) or {})} for n in spec.nodes}


def missing_required_params(spec: WorkflowSpec, node_params: dict[str, dict[str, Any]] | None) -> list[str]:
    errors = []
    eff = effective_params(spec, node_params)
    for n in spec.nodes:
        for p in STEP_TYPES[n.type].params:
            val = eff[n.id].get(p.key)
            if p.required and (val is None or (isinstance(val, str) and not val.strip())):
                errors.append(f"节点 {n.title or n.id} 需要填写「{p.title}」")
    return errors


def build_pipeline(
    spec: WorkflowSpec, settings: Settings, node_params: dict[str, dict[str, Any]] | None = None
) -> Pipeline:
    ensure_valid(spec)
    eff = effective_params(spec, node_params)
    incoming: dict[str, dict[str, tuple[str, str]]] = {n.id: {} for n in spec.nodes}
    for e in spec.edges:
        incoming[e.target][e.targetHandle] = (e.source, e.sourceHandle)
    nodes = [
        Node(id=n.id, stage=STEP_TYPES[n.type](), params=expand_dotted(eff[n.id]), inputs=incoming[n.id])
        for n in topo_order(spec)
    ]
    return Pipeline(settings=settings, nodes=nodes)


# ---------------------------------------------------------------- 存取


def _user_dir(settings: Settings) -> Path:
    return settings.abs_path(settings.workflows_dir)


def _load_file(p: Path) -> WorkflowSpec:
    return WorkflowSpec.model_validate(json.loads(p.read_text(encoding="utf-8")))


def builtin_ids() -> set[str]:
    return {p.stem for p in BUILTIN_DIR.glob("*.json")}


def list_workflows(settings: Settings) -> list[dict[str, Any]]:
    """内置 + 用户工作流的摘要(内置在前)。损坏的用户文件跳过并标注。"""
    out: list[dict[str, Any]] = []
    for builtin, folder in ((True, BUILTIN_DIR), (False, _user_dir(settings))):
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.json")):
            try:
                wf = _load_file(p)
            except (OSError, ValueError) as e:
                out.append({"id": p.stem, "title": p.stem, "builtin": builtin, "error": str(e)})
                continue
            out.append(
                {
                    "id": wf.id,
                    "title": wf.title,
                    "description": wf.description,
                    "builtin": builtin,
                    "node_count": len(wf.nodes),
                }
            )
    return out


def get_workflow(settings: Settings, workflow_id: str) -> WorkflowSpec:
    if not _WORKFLOW_ID.fullmatch(workflow_id):
        raise KeyError(workflow_id)
    for folder in (BUILTIN_DIR, _user_dir(settings)):
        p = folder / f"{workflow_id}.json"
        if p.is_file():
            return _load_file(p)
    raise KeyError(workflow_id)


def save_workflow(settings: Settings, spec: WorkflowSpec) -> Path:
    ensure_valid(spec)
    if spec.id in builtin_ids():
        raise WorkflowError([f"不能覆盖内置模板 {spec.id},请换一个 id 另存"])
    folder = _user_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{spec.id}.json"
    atomic_write_text(p, spec.model_dump_json(indent=2, exclude_none=True))
    return p


def delete_workflow(settings: Settings, workflow_id: str) -> None:
    if workflow_id in builtin_ids():
        raise WorkflowError(["内置模板不能删除"])
    p = _user_dir(settings) / f"{workflow_id}.json"
    if not _WORKFLOW_ID.fullmatch(workflow_id) or not p.is_file():
        raise KeyError(workflow_id)
    p.unlink()


def resolve_job_workflow(settings: Settings, job_dir: Path, ref: str | dict[str, Any]) -> WorkflowSpec:
    """任务用的工作流:已有快照优先(续跑稳定);否则按 id / 文件路径 / 内联 dict 解析并写快照。"""
    snap = job_dir / JOB_SNAPSHOT
    if snap.is_file():
        return _load_file(snap)
    if isinstance(ref, dict):
        spec = WorkflowSpec.model_validate(ref)
    elif ref.endswith(".json"):
        spec = _load_file(settings.abs_path(Path(ref)))
    else:
        try:
            spec = get_workflow(settings, ref)
        except KeyError as e:
            raise WorkflowError([f"工作流不存在: {ref}"]) from e
    ensure_valid(spec)
    job_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(snap, spec.model_dump_json(indent=2, exclude_none=True))
    return spec


def load_job_workflow(job_dir: Path) -> WorkflowSpec | None:
    snap = job_dir / JOB_SNAPSHOT
    if not snap.is_file():
        return None
    try:
        return _load_file(snap)
    except (OSError, ValueError):
        return None
