FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# git/node/uv: para o run_command e para servidores MCP via stdio (npx / uvx).
# tesseract: OCR de PDF escaneado (ver app/ocr.py). No app desktop quem faz isso é o próprio
# Windows; aqui é o pacote do apt, ~30 MB com o idioma português.
# safe.directory: o bind mount do Windows tem outro dono; sem isso o git recusa o repositório.
# tzdata: o TZ do compose só vale com as zonas instaladas (o "hoje" do cronograma da tela Estudos é o dia daqui).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git nodejs npm curl ca-certificates tesseract-ocr tesseract-ocr-por tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv \
 && git config --system safe.directory "*"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Navegador integrado: só o chromium-headless-shell (1/3 do Chromium completo) + libs do sistema.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/*
COPY . .
# O volume forja-data nasce com o dono e o modo deste /data: 777 deixa rodar com qualquer UID
# (FORJA_UID no Linux). /data/home é a home desse usuário (FORJA_HOME).
RUN mkdir -p /data/home && chmod -R 777 /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
