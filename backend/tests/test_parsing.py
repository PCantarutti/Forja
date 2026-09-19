import pytest

from app.parsing import LoopDetector, detect_promise, parse_text_tool_calls, split_think

NAMES = ["list_dir", "read_file", "write_file", "edit_file"]


# ------------------------------------------------ parser: 3 formatos

def test_tool_call_tags_hermes():
    text = 'Criando.\n<tool_call>\n{"name": "write_file", "arguments": {"path": "calc.py", "content": "x = 1\n"}}\n</tool_call>'
    calls, rest = parse_text_tool_calls(text, NAMES)
    assert calls == [{"name": "write_file", "arguments": {"path": "calc.py", "content": "x = 1\n"}}]
    assert rest == "Criando."


def test_tool_call_tags_unclosed_and_string_args():
    text = '<tool_call>{"name": "read_file", "arguments": "{\\"path\\": \\"a.py\\"}"}'
    calls, _ = parse_text_tool_calls(text, NAMES)
    assert calls == [{"name": "read_file", "arguments": {"path": "a.py"}}]


def test_json_fence():
    text = 'Vou ler:\n```json\n{"name": "read_file", "arguments": {"path": "calc.py"}}\n```'
    calls, rest = parse_text_tool_calls(text, NAMES)
    assert calls == [{"name": "read_file", "arguments": {"path": "calc.py"}}]
    assert rest == "Vou ler:"


def test_json_fence_unknown_name_ignored():
    text = '```json\n{"name": "fulano", "arguments": {}}\n```'
    assert parse_text_tool_calls(text, NAMES)[0] == []


def test_json_fence_openai_shape_list():
    text = '```json\n[{"function": {"name": "list_dir", "arguments": {}}}]\n```'
    assert parse_text_tool_calls(text, NAMES)[0] == [{"name": "list_dir", "arguments": {}}]


def test_xml_qwen_coder():
    text = ("<tool_call>\n<function=write_file>\n<parameter=path>\ncalc.py\n</parameter>\n"
            "<parameter=content>\ndef soma(a, b):\n    return a + b\n</parameter>\n</function>\n</tool_call>")
    calls, _ = parse_text_tool_calls(text, NAMES)
    assert calls == [{"name": "write_file",
                      "arguments": {"path": "calc.py", "content": "def soma(a, b):\n    return a + b"}}]


def test_xml_name_attr():
    text = '<tool name="edit_file"><param name="path">a.py</param><param name="old_str">x</param><param name="new_str">y</param></tool>'
    calls, _ = parse_text_tool_calls(text, NAMES)
    assert calls == [{"name": "edit_file", "arguments": {"path": "a.py", "old_str": "x", "new_str": "y"}}]


def test_xml_simple_tags():
    text = "Ok.\n<write_file><path>calc.py</path><content>x = 1</content></write_file>"
    calls, rest = parse_text_tool_calls(text, NAMES)
    assert calls == [{"name": "write_file", "arguments": {"path": "calc.py", "content": "x = 1"}}]
    assert rest == "Ok."


def test_plain_text_has_no_calls():
    assert parse_text_tool_calls("Olá! Como posso ajudar?", NAMES) == ([], "Olá! Como posso ajudar?")


def test_think_is_ignored():
    text = '<think>talvez <tool_call>{"name":"list_dir","arguments":{}}</tool_call></think>Pronto.'
    assert parse_text_tool_calls(text, NAMES)[0] == []
    assert split_think(text)[1] == "Pronto."


# ------------------------------------------------ promessa sem ação

@pytest.mark.parametrize("text", [
    "Vou criar o arquivo calc.py agora.",
    "Certo! Deixa eu corrigir isso.",
    "Deixe-me ler o arquivo primeiro.",
    "I'll write the file now.",
    "Let me create calc.py for you.",
    "Agora vou adicionar a função de subtração.",
])
def test_promise_detected(text):
    assert detect_promise(text)


@pytest.mark.parametrize("text", [
    "O arquivo calc.py foi criado com sucesso.",
    "Pronto! Adicionei a função subtracao.",
    "Python é uma linguagem de programação.",
    "<think>vou criar o arquivo</think>Feito.",
    "```python\n# vou criar isso depois\n```\nEsse é o código.",
])
def test_promise_not_detected(text):
    assert not detect_promise(text)


# ------------------------------------------------ loop

def test_loop_detector_triggers_on_third_identical_call():
    d = LoopDetector()
    assert not d.record("read_file", {"path": "a"})
    assert not d.record("read_file", {"path": "a"})
    assert d.record("read_file", {"path": "a"})


def test_loop_detector_resets_on_different_call():
    d = LoopDetector()
    d.record("read_file", {"path": "a"})
    d.record("read_file", {"path": "a"})
    assert not d.record("read_file", {"path": "b"})
    assert not d.record("read_file", {"path": "a"})


def test_loop_detector_arg_order_irrelevant():
    d = LoopDetector()
    d.record("x", {"a": 1, "b": 2})
    d.record("x", {"b": 2, "a": 1})
    assert d.record("x", {"a": 1, "b": 2})
