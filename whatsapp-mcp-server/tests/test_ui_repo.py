"""Repository guards for the interface (MIG-08, MIG-09, MIG-19, MIG-20, MIG-22): the old toolkit is gone, the old
packages are gone, the frontend carries no WhatsApp brand asset and the operator texts of the server are English."""
import ast
import re
import subprocess
import tomllib
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
ROOT = SERVER.parent
# The word is spelled in two pieces so that this file does not contain it.
OLD_TOOLKIT = re.compile("fl" + "et", re.I)
# Decision records are the one place the old toolkit may be named (AD-009 and AD-011). They live in .specs/, which is
# not tracked, so today the scan finds nothing there; the prefix is allowed so that tracking .specs/ later does not
# turn history into a failure.
DECISIONS_PREFIXES = (".specs/",)
# This file is the other exception: the guards are named after the word (the names are fixed by the checks), so the
# path itself is allowed, and only this exact path.
THIS_FILE = "whatsapp-mcp-server/tests/test_ui_repo.py"


def tracked(*paths, others=False):
    args = ["git", "ls-files", "-z", "--cached"] + (["--others", "--exclude-standard"] if others else []) + ["--", *paths]
    out = subprocess.run(args, cwd=ROOT, check=True, capture_output=True).stdout.decode()
    return [p for p in out.split("\0") if p and (ROOT / p).is_file()]


def test_repo_has_no_flet_outside_decisions():  # MIG-08
    hits = []
    for path in tracked(others=True):
        if path.startswith(DECISIONS_PREFIXES) or path == THIS_FILE:
            continue
        data = (ROOT / path).read_bytes()
        if b"\0" in data:
            continue
        if OLD_TOOLKIT.search(path) or OLD_TOOLKIT.search(data.decode("utf-8", "replace")):
            hits.append(path)
    assert hits == [], f"the old toolkit is named in: {hits}"


def test_lock_and_pyproject_list_no_flet():  # MIG-09
    lock = (SERVER / "uv.lock").read_text(encoding="utf-8")
    assert not OLD_TOOLKIT.search(lock), "uv.lock lists the old toolkit or a package of it"
    project = tomllib.loads((SERVER / "pyproject.toml").read_text(encoding="utf-8"))
    assert not OLD_TOOLKIT.search((SERVER / "pyproject.toml").read_text(encoding="utf-8"))
    groups = set(project.get("dependency-groups", {}))
    assert groups <= {"dev"}, f"only the dev group may remain, found {groups}"
    assert "optional-dependencies" not in project


def test_old_ui_packages_are_gone():  # MIG-22
    for name in ("core", "app", "assets"):
        assert not (SERVER / "ui" / name).exists(), f"ui/{name} must not exist"
    assert tracked("whatsapp-mcp-server/ui/core", "whatsapp-mcp-server/ui/app", "whatsapp-mcp-server/ui/assets", others=True) == []
    # the old toolkit's own tests and end-to-end folder are gone, tracked or not
    assert sorted(p.name for p in (SERVER / "tests").glob("test_ui_app_*")) == []
    assert not (SERVER / "tests" / "e2e").exists(), "tests/e2e must not exist"
    assert tracked("whatsapp-mcp-server/tests/e2e", "whatsapp-mcp-server/tests/test_ui_app_*", others=True) == []
    # the old toolkit's tests are gone too: nothing under tests/ imports or names it (the scan above covers the text)
    for test in (SERVER / "tests").glob("test_ui_*.py"):
        assert not re.search(r"^\s*(import|from)\s+" + "fl" + r"et\b", test.read_text(encoding="utf-8"), re.M), test.name


BRAND_NAME = re.compile(r"whats-?app|(^|[-_./])wa[-_]?(logo|icon|mark|glyph)", re.I)
BRAND_GREEN = re.compile(r"#?25d366|#?128c7e|#?075e54", re.I)
IMAGE_SUFFIXES = {".svg", ".png", ".ico", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".icns"}


def test_frontend_has_no_whatsapp_brand_assets():  # MIG-19
    offenders = []
    for path in tracked("whatsapp-mcp-server/frontend", others=True):
        if path.startswith("whatsapp-mcp-server/frontend/node_modules/"):
            continue
        p = ROOT / path
        rel = path[len("whatsapp-mcp-server/frontend/"):]
        if p.suffix.lower() in IMAGE_SUFFIXES and BRAND_NAME.search(rel):
            offenders.append(rel)
        elif p.suffix.lower() == ".svg" and (BRAND_NAME.search(p.read_text("utf-8", "replace")) or BRAND_GREEN.search(p.read_text("utf-8", "replace"))):
            offenders.append(rel)
    index = (SERVER / "frontend" / "index.html").read_text(encoding="utf-8")
    for link in re.findall(r"<link\b[^>]*>", index, re.I):
        if re.search(r"rel=[\"'][^\"']*icon", link, re.I) and BRAND_NAME.search(link):
            offenders.append("index.html: " + link)
    assert offenders == [], f"WhatsApp brand assets in the frontend: {offenders}"


PROJECT_NAME = "WhatsApp MCP"


def test_the_only_whatsapp_text_in_the_frontend_is_the_project_name():  # MIG-19 (text prong)
    """"WhatsApp MCP" is the project's own name and may name the interface; the product's name, or anything implying the
    official app, may not appear."""
    frontend = SERVER / "frontend"
    sources = [frontend / "index.html", *(p for p in (frontend / "src").rglob("*") if p.is_file()),
               *(p for p in (frontend / "public").rglob("*") if p.is_file() and p.suffix in {".html", ".json", ".svg", ".txt", ".webmanifest"})]
    seen = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        stripped = text.replace(PROJECT_NAME, "")
        assert not re.search(r"whats\W?app", stripped, re.I), f"{path.relative_to(frontend)} names WhatsApp beyond the project name"
        if PROJECT_NAME in text:
            seen.append(path.relative_to(frontend).as_posix())
    assert sorted(seen) == ["index.html", "src/i18n/en.ts"], seen


def test_the_whatsapp_text_check_flags_the_product_name():  # MIG-19
    assert re.search(r"whats\W?app", "Sign in to WhatsApp".replace(PROJECT_NAME, ""), re.I)
    assert re.search(r"whats\W?app", "WhatsApp Web settings".replace(PROJECT_NAME, ""), re.I)
    assert not re.search(r"whats\W?app", "WhatsApp MCP Settings".replace(PROJECT_NAME, ""), re.I)


# Portuguese is detected two ways: any letter only Portuguese uses (accents, cedilla, tilde), or a whole word from a list of
# words with no English meaning. The list is wide on purpose; a word that is also English (com, a, no, me...) is left out.
PORTUGUESE_WORDS = (
    "não|nao|você|voce|para|pra|erro|erros|senha|arquivo|arquivos|mensagem|mensagens|configuração|configurações|salvar|salvo|"
    "salvando|inválido|inválida|invalido|invalida|obrigatório|obrigatorio|campo|campos|porta|deve|devem|usar|uma|dos|das|nos|nas|"
    "sua|seu|suas|seus|ainda|apenas|quando|sem|ou|tente|tentar|novamente|mais|menos|tarde|cedo|falha|falhou|ao|aos|gravar|gravado|"
    "gravando|ocorreu|sucesso|aguarde|carregando|nenhum|nenhuma|também|tambem|porque|pelo|pela|pelos|pelas|como|mas|foi|são|está|"
    "estão|será|isso|esse|essa|este|esta|usuário|usuario|conexão|conexao|inicie|feche|abra|abrir|fechar|aviso|ativar|desativar|"
    "padrão|padrao|valor|valores|todos|todas|cada|existe|existem|entrar|sair|sessão|sessao|expirou|expirada|digite|informe|"
    "escolha|selecione|opção|opcao|opções|opcoes|já|ja|pode|podem|precisa|precisam|aqui|agora|depois|antes|sobre|entre|até|"
    "desde|durante|seguinte|seguintes|permitido|permitida|proibido|bloqueado|bloqueada|enviar|enviado|enviada|receber|lista|vazio|vazia"
)
PORTUGUESE = re.compile(r"[áàâãäéêèíìîóôõòúùûüçñ]|\b(" + PORTUGUESE_WORDS + r")\b", re.I)


def operator_literals():
    files = [p for p in (SERVER / "ui").rglob("*.py") if "web" not in p.relative_to(SERVER / "ui").parts]
    files.append(SERVER / "uisettings.py")
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield f.relative_to(SERVER), node.lineno, node.value


def test_server_operator_texts_are_english():  # MIG-20
    hits = [(str(f), n, v[:80]) for f, n, v in operator_literals() if PORTUGUESE.search(v)]
    assert hits == [], f"Portuguese in string literals: {hits}"


def test_the_portuguese_detector_flags_portuguese_and_passes_english():  # MIG-20
    for text in ("Tente novamente mais tarde", "Falha ao gravar", "Senha incorreta", "Nenhum arquivo", "Ação inválida",
                 "Conexão perdida", "Digite o valor"):
        assert PORTUGUESE.search(text), text
    for text in ("Try again later", "Failed to write the file", "Password is wrong", "No file", "Invalid action",
                 "WHATSAPP_UI_PORT must be a whole number", "Sign in to continue.", "Request refused; reload the page."):
        assert not PORTUGUESE.search(text), text
