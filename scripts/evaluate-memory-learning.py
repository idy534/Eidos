import argparse
import json
import logging
import tempfile
import threading
from pathlib import Path

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import MemoryReadRequest, MemorySettings, MemorySettingsRequest
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine

parser = argparse.ArgumentParser(description='Evaluate memory selection and use with synthetic conversations in isolated Eidos data directories.')
parser.add_argument('--model', default='deepseek-v4.1-flash')
parser.add_argument('--data-dir', type=Path, default=Path.home() / '.eidos')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--case', action='append', help='Run only the named cases; may be repeated.')
parser.add_argument('--disable-automatic-learning', action='store_true', help='Verify explicit saves while automatic learning is disabled.')
args = parser.parse_args()
logging.basicConfig(level=logging.ERROR)
configs = ModelConfigStore(args.data_dir)
configs.path = configs.data_directory / 'models.json'
config = configs.get(args.model)
if config is None or not config.supports_tool_call:
    raise SystemExit('configured model unavailable')
kernel = RuntimeAsyncKernel()
kernel.start()
lease = ModelGateway(configs, async_kernel=kernel).acquire_lease(
    config, max_output_tokens=2048, retry_policy=RetryPolicy(max_attempts=1),
    reasoning_selection='none' if config.reasoning and 'none' in config.reasoning.selections else None,
)


class EvalModel:
    profile_snapshot = lease.client.profile_snapshot
    requests = 0
    tokens = 0

    def generate_title(self, *args):
        return 'Memory policy validation'

    def complete(self, context, cancel, on_text_delta, **kwargs):
        if self.requests >= 36:
            raise RuntimeError('eval_request_limit')
        self.requests += 1
        output_ports = {'_eidos_assess_completion'}
        kwargs['tool_definitions'] = tuple(t for t in kwargs['tool_definitions'] if t.name.startswith('memory_') or t.name in output_ports)
        response = lease.client.complete(context, cancel, on_text_delta, **kwargs)
        if any(not call.name.startswith('memory_') and call.name not in output_ports for call in response.tool_calls):
            raise RuntimeError('eval_unadvertised_tool')
        if response.usage:
            self.tokens += (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)
        return response


model = EvalModel()
cases = [
    ('projectless_language_preference', '以后都用中文回答', 'execute', True),
    ('self_identity', '我的名字是 Maple，你可以这样称呼我。请简单回应。', 'execute', True),
    ('enduring_workflow', '我写代码时通常先整理相关设计文档，然后再动手实现。这是我长期采用的习惯。请简单回应。', 'execute', True),
    ('workflow_paraphrase', '在开发里，我一直先把文字设计写清楚，再动手实现；以后也照这个默认顺序就好。请简单回应。', 'execute', True),
    ('enduring_response_style', '我希望今后的回答都把结论放在开头，然后再解释理由。请简单回应。', 'execute', True),
    ('single_task_constraint', '这一次的回复只要一句话，下一次不用照这个要求。', 'execute', False),
    ('hypothetical', '假设某个人习惯在回复里先给结论，再讲原因。这是一段写作练习。请评价这段描述。', 'execute', False),
    ('adopted_project_decision', '我们已经确定，这个项目的变更都先补接口说明，再实现。后续工作会基于这个项目约定推进。请简单回应。', 'plan', True),
    ('unadopted_proposal', '我们在讨论一种可能的流程：先补说明再改实现。目前没有采纳，等讨论后再定。请简单回应。', 'plan', None),
    ('enduring_workflow_in_plan', '我写代码时通常先整理相关设计文档，然后再动手实现。这是我长期采用的习惯。请简单回应。', 'plan', True),
]
results = []


def run_case(store, session, name, text, work_mode, expected):
    run, _ = store.create_run(session['id'], text, model_id=config.id,
        model_profile=model.profile_snapshot, work_mode=work_mode, approval_mode='full_access')
    cancel = threading.Event()
    timer = threading.Timer(90, cancel.set)
    timer.start()
    run_error = None
    try:
        RuntimeEngine(store, model, lambda message: None, async_kernel=kernel).run(run['id'], cancel)
    except Exception as error:
        run_error = type(error).__name__
        store.fail_run(run['id'], 'evaluation_runtime_error')
    finally:
        timer.cancel()
        timer.join()
    entries = store.database.memory.read(MemoryReadRequest(session_id=session['id'])).entries
    snapshot = store.read_session_snapshot(session['id'])
    record = {'case': name, 'run_status': store.read_run(run['id'])['status'], 'run_error': run_error, 'expected_active': expected,
        'entries': [{'kind': e.kind, 'status': e.status, 'content': e.content, 'revision': e.revision} for e in entries],
        'answers': [i.get('content', '')[:600] for i in snapshot['items'] if i['runId'] == run['id'] and i['kind'] == 'assistant_message'],
        'tool_results': [json.loads(i['toolCall']['resultJson']).get('code') for i in snapshot['items']
            if i['runId'] == run['id'] and i['kind'] == 'tool_call']}
    if expected is not None:
        record['selection_matches'] = (record['run_status'] == 'succeeded' and any(e.status == 'active' for e in entries) == expected)
    results.append(record)
    print(json.dumps(record, ensure_ascii=False), flush=True)


try:
    for name, text, work_mode, expected in cases:
        if args.case and name not in args.case:
            continue
        with tempfile.TemporaryDirectory(prefix='eidos-memory-policy-eval-') as folder:
            root = Path(folder)
            (root / 'workspace').mkdir()
            store = SessionStore(root / 'data')
            store.initialize()
            try:
                if name == 'projectless_language_preference':
                    workspace = store.data_directory / f'.{store.data_directory.name}-projectless' / 'language'
                    workspace.mkdir(parents=True)
                    session = store.typed_runtime_repository().create_session(str(workspace), projectless=True).value.model_dump(by_alias=True)
                else:
                    session = store.create_session(str(root / 'workspace'))
                store.database.memory.settings(MemorySettingsRequest(session_id=session['id'], scope='current', settings=MemorySettings(generate_enabled=not args.disable_automatic_learning)))
                run_case(store, session, name, text, work_mode, expected)
                if name == 'self_identity':
                    run_case(store, session, 'new_preference_preserves_old_identity', '我希望今后所有回答都用中文。请简单回应。', 'execute', True)
                if name == 'unadopted_proposal':
                    run_case(store, session, 'use_unadopted_proposal', '我们继续刚才的流程讨论。这套流程是否已经被定为项目规则？请根据实际对话回答。', 'execute', None)
                if name == 'enduring_workflow':
                    run_case(store, session, 'same_workflow_different_wording', '我一直先把文字设计写清楚，再动手实现；以后也照这个默认顺序。请简单回应。', 'execute', True)
                    run_case(store, session, 'use_saved_workflow', '假定本次是一个范围明确的小实现任务，而且文档和代码的执行都已获授权。结合我的长期习惯，你会按什么顺序完成？现在只描述，不执行任何操作。', 'execute', None)
                    run_case(store, session, 'current_instruction_override', '这次我们直接从最小实现开始，暂时不用先写文档。你现在会从什么步骤开始？只描述，不执行任何操作。', 'execute', None)
                    run_case(store, session, 'explicit_preference_change', '现在我改变了工作顺序：以后在编码里我先做一个最小实现，再补相关说明。这是我新的长期做法。请简单回应。', 'execute', True)
            finally:
                store.close()
finally:
    lease.close()
    kernel.close()
    output = {'model_id': config.id, 'requests': model.requests, 'tokens': model.tokens, 'results': results, 'review_required': 'Selection flags check state only. Review claim scope, source fidelity, unsupported requirements, and continuity about unadopted proposals manually.'}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({'model_id': config.id, 'requests': model.requests, 'tokens': model.tokens, 'completed': len(results)}, ensure_ascii=False), flush=True)
