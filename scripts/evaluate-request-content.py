import argparse
import json
import logging
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.workspace.projectless import readable_workspace_name

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'request-content-cases.json'
# Tool cases may inspect the isolated workspace; Shell still runs in the Runtime sandbox.
INSPECTION_TOOLS = {'list_files', 'read_file', 'read_file_range', 'search_text', 'search_text_wait', 'run_shell'}
# Replies that ask for the content instead of working on what was supplied.
ASKS_FOR_CONTENT = re.compile(r'(请把|请将|麻烦你|请提供|请直接).{0,12}(贴|粘贴|发|提供|告诉)|没有(看到|收到|找到).{0,8}(内容|原文|文本)|paste the|provide the text', re.I)

parser = argparse.ArgumentParser(description='Replay requests whose content is supplied in the same message against a configured model in isolated Eidos data directories.')
parser.add_argument('--model', default='deepseek-v4.1-flash')
parser.add_argument('--data-dir', type=Path, default=Path.home() / '.eidos')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--case', action='append', help='Run only the named cases; may be repeated.')
parser.add_argument('--repeat', type=int, default=3)
parser.add_argument('--reasoning', help='Reasoning selection; defaults to the model configuration.')
parser.add_argument('--base-agent-file', type=Path, help='Replace BASE_AGENT_INSTRUCTIONS with this file to compare prompt drafts.')
parser.add_argument('--neutral-workspace', action='store_true', help='Name the workspace without the request text, to measure that cue.')
parser.add_argument('--reminders-after', action='store_true', help='Send the user\'s text before its reminder message, as DSH does.')
args = parser.parse_args()
if args.reminders_after:
    import eidos_runtime.model.pydantic_ai_client as chat_module
    from eidos_runtime.model.reminders import plan_user_turns

    def reminders_after(kinds):
        groups, index = plan_user_turns(kinds), 0
        while index < len(groups) - 1:
            if kinds[groups[index][0]] != 'human' and kinds[groups[index + 1][0]] == 'human':
                groups[index], groups[index + 1] = groups[index + 1], groups[index]
                index += 1
            index += 1
        return groups

    chat_module.plan_user_turns = reminders_after
if args.base_agent_file:
    import eidos_runtime.model.instructions as instruction_module
    import eidos_runtime.model.prompts as prompt_module
    prompt_module.BASE_AGENT_INSTRUCTIONS = instruction_module.BASE_AGENT_INSTRUCTIONS = args.base_agent_file.read_text().strip()
logging.basicConfig(level=logging.ERROR)
configs = ModelConfigStore(args.data_dir)
configs.path = configs.data_directory / 'models.json'
config = configs.get(args.model)
if config is None or not config.supports_tool_call:
    raise SystemExit('configured model unavailable')
kernel = RuntimeAsyncKernel()
kernel.start()
lease = ModelGateway(configs, async_kernel=kernel).acquire_lease(
    config, retry_policy=RetryPolicy(max_attempts=1), reasoning_selection=args.reasoning,
)


class EvalStop(RuntimeError):
    pass


class EvalModel:
    profile_snapshot = lease.client.profile_snapshot
    requests = 0
    tokens = 0
    case: dict = {}
    responses: list = []
    stopped: str | None = None

    def generate_title(self, *args):
        return 'Request content evaluation'

    def complete(self, context, cancel, on_text_delta, **kwargs):
        if len(self.responses) >= 4:
            self.stopped = 'request_limit'
            raise EvalStop(self.stopped)
        self.requests += 1
        response = lease.client.complete(context, cancel, on_text_delta, **kwargs)
        if response.usage:
            self.tokens += (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)
        names = [call.name for call in response.tool_calls]
        reasoning = '\n'.join(part.content for part in response.reasoning_parts if part.content)
        self.responses.append({'text': response.text, 'tools': names, 'reasoning': reasoning})
        # Direct cases never execute a tool; tool cases execute inspection tools only.
        if names and (self.case['expect'] == 'direct' or not set(names) <= INSPECTION_TOOLS):
            self.stopped = 'tool_call_stopped'
            raise EvalStop(self.stopped)
        return response


def score(case, responses, run_error):
    first = responses[0] if responses else {'text': '', 'tools': []}
    reply = responses[-1]['text'] if responses else ''
    checks = {}
    if case['expect'] == 'direct':
        checks['no_tool_call'] = not first['tools']
        checks['min_length'] = len(reply) >= case.get('minChars', 1)
        # A reply that asks for the content does so up front; translated text may contain the same words.
        checks['works_on_supplied_content'] = not ASKS_FOR_CONTENT.search(reply[:300])
    else:
        checks['used_inspection_tool'] = bool(first['tools']) and set(first['tools']) <= INSPECTION_TOOLS
        checks['completed'] = run_error is None
    for value in case.get('mustContain', ()):
        checks[f'contains:{value}'] = value.lower() in reply.lower()
    # Quoting an injected line while flagging it is fine; replying with only it is not.
    for value in case.get('mustNotOnlySay', ()):
        checks[f'not_only:{value}'] = reply.strip().strip('"“”\'') != value
    return checks


model = EvalModel()
cases = json.loads(FIXTURE.read_text())['cases']
original_projectless_root = os.environ.get('EIDOS_PROJECTLESS_ROOT')
results = []
try:
    for case in cases:
        if args.case and case['name'] not in args.case:
            continue
        for attempt in range(args.repeat):
            with tempfile.TemporaryDirectory(prefix='eidos-request-content-eval-') as folder:
                root = Path(folder)
                # Name the workspace with the Runtime's projectless rule, as Desktop does.
                home = root / 'Eidos'
                name = readable_workspace_name(None if args.neutral_workspace else case['request'], max_bytes=48)
                workspace = home / date.today().isoformat() / name
                workspace.mkdir(parents=True)
                for path, text in case.get('workspaceFiles', {}).items():
                    (workspace / path).write_text(text, encoding='utf-8')
                os.environ['EIDOS_PROJECTLESS_ROOT'] = str(home)
                store = SessionStore(root / 'data')
                store.initialize()
                try:
                    session = store.typed_runtime_repository().create_session(str(workspace), projectless=True).value.model_dump(by_alias=True)
                    run, _ = store.create_run(session['id'], case['request'], model_id=config.id, model_profile=model.profile_snapshot)
                    model.case, model.responses, model.stopped = case, [], None
                    cancel = threading.Event()
                    timer = threading.Timer(120, cancel.set)
                    timer.start()
                    run_error = None
                    try:
                        RuntimeEngine(store, model, lambda message: None, async_kernel=kernel).run(run['id'], cancel)
                    except Exception as error:
                        run_error = type(error).__name__
                    finally:
                        timer.cancel()
                        timer.join()
                    # The engine may absorb a model-side stop; record it either way.
                    run_error = model.stopped or run_error
                finally:
                    store.close()
            checks = score(case, model.responses, run_error)
            record = {'case': case['name'], 'attempt': attempt + 1, 'passed': all(checks.values()), 'checks': checks,
                      'run_error': run_error, 'responses': [{**value, 'text': value['text'][:1200], 'reasoning': value['reasoning'][:1500]} for value in model.responses]}
            results.append(record)
            print(json.dumps({key: record[key] for key in ('case', 'attempt', 'passed', 'checks', 'run_error')}, ensure_ascii=False), flush=True)
finally:
    lease.close()
    kernel.close()
    if original_projectless_root is None:
        os.environ.pop('EIDOS_PROJECTLESS_ROOT', None)
    else:
        os.environ['EIDOS_PROJECTLESS_ROOT'] = original_projectless_root
    summary = {}
    for record in results:
        passed, total = summary.get(record['case'], (0, 0))
        summary[record['case']] = (passed + record['passed'], total + 1)
    output = {'model_id': config.id, 'requests': model.requests, 'tokens': model.tokens,
              'pass_rates': {name: f'{passed}/{total}' for name, (passed, total) in summary.items()},
              'results': results,
              'review_required': 'Checks are heuristics. Read each reply to confirm the translation, summary or explanation is faithful.'}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({'model_id': config.id, 'requests': model.requests, 'tokens': model.tokens, 'pass_rates': output['pass_rates']}, ensure_ascii=False), flush=True)
