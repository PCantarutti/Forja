import os
import tempfile

# db.py cria o SQLite no import; nos testes, num diretório temporário.
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="forja-test-"), "forja.db"))
