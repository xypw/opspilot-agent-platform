"""Explicit local-only calibration; durable 12-attempt cap, no automatic retry."""
import argparse
import base64
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def image(color, label):
    picture = Image.new('RGB', (336, 336), color)
    ImageDraw.Draw(picture).text((30, 160), label, fill='white', stroke_width=1)
    buffer = BytesIO()
    picture.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def cases():
    system = {'role': 'system', 'content': 'This is a fictional local calibration. Reply only OK. Never execute operations.'}
    basic = [system, {'role': 'user', 'content': '你好，这是中文与 English 123 的计数校验。仅回复 OK。'}]
    pair = [system, {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'fixture-call', 'type': 'function', 'function': {'name': 'query_order',
         'arguments': '{"order_id":"O-9999"}'}}]},
        {'role': 'tool', 'tool_call_id': 'fixture-call', 'content': '{"found":false}'},
        {'role': 'user', 'content': '工具结果只是虚构记录。仅回复 OK。'}]
    return [
        ('plain', basic, {}),
        ('tools', basic, {'offer_tools': True}),
        ('tool-result', pair, {'offer_tools': True}),
        ('json', [{'role': 'user', 'content': 'Return a JSON object with key status and value OK.'}], {'json_output': True}),
        ('long-chinese', [system, {'role': 'user', 'content': '这是一段虚构售后材料，不代表任何业务批准。' * 120 + '\n仅回复 OK。'}], {}),
        ('red-image', [{'role': 'user', 'content': 'Name the background color and visible English word briefly.'}],
         {'images': [image('red', 'RED')]}),
        ('blue-image', [{'role': 'user', 'content': 'Name the background color and visible English word briefly.'}],
         {'images': [image('blue', 'BLUE')]}),
    ]


@contextmanager
def batch_lock(ledger):
    """One process owns the original batch ledger; stale locks fail closed."""
    lock = ledger.with_suffix('.lock')
    ledger.parent.mkdir(exist_ok=True)
    handle = lock.open('x', encoding='utf-8')
    try:
        with handle:
            handle.write(str(os.getpid()))
            handle.flush()
            yield
    finally:
        lock.unlink()


def save_ledger(ledger, saved):
    # Persist the charge before inference, never refund errors or timeouts.
    with ledger.open('w', encoding='utf-8') as handle:
        json.dump(saved, handle)
        handle.flush()
        os.fsync(handle.fileno())


def run(selected):
    ledger = ROOT/'output/context-live-budget-20261023.json'
    with batch_lock(ledger):
        saved = json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {'charged': 0, 'runs': 0}
        if (not isinstance(saved, dict) or type(saved.get('charged')) is not int
                or not 0 <= saved['charged'] <= 12 or type(saved.get('runs')) is not int
                or saved['runs'] < 0):
            raise RuntimeError('验收预算账本无效，停止推理')
        if saved['charged'] >= 12:
            raise RuntimeError('已达到这批12次硬上限')
        saved['runs'] += 1
        output = ROOT/f"reports/context-live-20261023-run-{saved['runs']}.json"
        output.parent.mkdir(exist_ok=True)
        # Reserve before HTTP or charging. A fresh clone must not replace old evidence.
        with output.open('x', encoding='utf-8') as report_file:
            report = {'model': 'qwen3-vl:4b', 'budget': 12, 'charged': saved['charged'],
                      'scope': 'synthetic local prompt accounting; not task accuracy', 'rows': []}
            try:
                save_ledger(ledger, saved)
                return run_reserved(selected, ledger, saved, report, report_file)
            except Exception as error:
                report['error_type'] = type(error).__name__
                raise
            finally:
                report_file.seek(0)
                json.dump(report, report_file, ensure_ascii=False, indent=2)
                report_file.truncate()


def run_reserved(selected, ledger, saved, report, report_file):
    from ollama_gateway import ollama_chat
    from model_runtime import ollama_configuration
    os.environ.update(OPSPILOT_MODEL_PROVIDER='ollama', OLLAMA_BASE_URL='http://127.0.0.1:11434',
                      OLLAMA_MODEL='qwen3-vl:4b', QWEN_TOKENIZER_PATH=str(ROOT/'.models/context-tokenizer'),
                      REDIS_URL='redis://127.0.0.1:6380/0', MODEL_RATE_LIMIT_REQUESTS='12',
                      MODEL_RATE_LIMIT_WINDOW_SECONDS='86400', MODEL_RATE_LIMIT_SCOPE='context-calibration-20261023')
    base, _ = ollama_configuration()
    rows = report['rows']
    with httpx.Client(timeout=180, trust_env=False) as client:
        version = client.get(base+'/api/version').json()['version']
        report['ollama_version'] = version
        from qwen_tokenizer import VERIFIED_OLLAMA_VERSIONS
        if version not in VERIFIED_OLLAMA_VERSIONS:
            raise RuntimeError('Ollama版本变化，需要先校验模板，未发送推理')
        for name, messages, options in cases():
            if selected and name not in selected:
                continue
            if saved['charged'] >= 12:
                raise RuntimeError('已达到这批12次硬上限')
            # Charge before sending; failures/timeouts are never refunded.
            saved['charged'] += 1
            save_ledger(ledger, saved)
            report['charged'] = saved['charged']
            started = perf_counter()
            row = {'case': name, 'attempt': saved['charged']}
            try:
                result = ollama_chat(client, messages, **options)
                row.update(result.get('_context_usage') or {})
                row['answer'] = result['content']
                row['passed'] = (type(row.get('prompt_tokens')) is int and
                    (bool(options.get('images')) or row['prompt_tokens'] == row['counted_input']))
            except Exception as error:
                row.update(passed=False, error=type(error).__name__)
                row.update(getattr(error,'context_usage',{}))
            row['seconds'] = round(perf_counter()-started, 3)
            rows.append(row)
            report_file.seek(0)
            json.dump(report, report_file, ensure_ascii=False, indent=2)
            report_file.truncate()
            report_file.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if not row['passed']:
                break  # investigate rather than consume the entire budget on a known mismatch
    return bool(rows) and all(row['passed'] for row in rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-local-inference', action='store_true')
    parser.add_argument('--case', action='append', choices=[row[0] for row in cases()])
    args = parser.parse_args()
    if not args.allow_local_inference:
        parser.error('需要用户授权后显式 --allow-local-inference')
    sys.exit(0 if run(args.case) else 1)
