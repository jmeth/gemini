.PHONY: check check-proxy install install-tools install-backend install-frontend build dev start stop restart status logs clean voice-model install-vision vision-model

# ── dirs & files ──────────────────────────────────────────────────────────────
PIDS   := .pids
LOGS   := .logs

# ── toolchain (mise pins python/node in mise.toml; uv manages the venv) ──────
MISE   := mise exec --
UV     := $(MISE) uv
PYTHON := $(UV) run python
NPM    := $(MISE) npm

# Build the venv with mise's python rather than whatever uv would discover.
MISE_PYTHON := $(shell mise which python 2>/dev/null)
ifneq ($(MISE_PYTHON),)
export UV_PYTHON := $(MISE_PYTHON)
endif

# ── install ───────────────────────────────────────────────────────────────────
check: check-proxy

check-proxy:
	@$(PYTHON) scripts/check_proxy.py

install: install-tools install-backend voice-model install-frontend

install-tools:
	mise install

# --inexact keeps the vision extra if it was installed separately.
install-backend:
	$(UV) sync --inexact

install-frontend:
	$(NPM) --prefix frontend install

# ── object detection (optional, heavy — pulls torch for the one-time export) ──
install-vision:
	$(UV) sync --inexact --extra vision

# Build the YOLO26n NCNN model up front so the first detection isn't a cold export.
vision-model:
	$(PYTHON) -c "from backend.vision.detector import detector; \
	    import sys; sys.exit(0 if detector.ensure_loaded() else 1)" \
	    && echo "YOLO26n NCNN model ready under models/" \
	    || echo "Model build failed — is ultralytics installed? (make install-vision)"

# ── vosk model for the Raspberry Pi voice listener ───────────────────────────
VOSK_MODEL := models/vosk-model-small-en-us-0.15
VOSK_URL   := https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip

voice-model:
	@if [ -d "$(VOSK_MODEL)" ]; then \
	    echo "Vosk model already present at $(VOSK_MODEL)"; \
	else \
	    echo "Fetching Vosk English model (~40MB) …"; \
	    mkdir -p models; \
	    curl -fsSL -o models/_vosk.zip "$(VOSK_URL)"; \
	    unzip -q models/_vosk.zip -d models; \
	    rm -f models/_vosk.zip; \
	    echo "Installed $(VOSK_MODEL)"; \
	fi

# ── build frontend ────────────────────────────────────────────────────────────
build:
	$(NPM) --prefix frontend run build

# ── dev (hot-reload both) ─────────────────────────────────────────────────────
dev: _mkdirs
	@$(MAKE) _stop-backend  2>/dev/null; true
	@$(MAKE) _stop-frontend 2>/dev/null; true
	@echo "Starting backend  (log: $(LOGS)/backend.log)"
	$(PYTHON) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload \
	    --timeout-graceful-shutdown 5 \
	    > $(LOGS)/backend.log 2>&1 & echo $$! > $(PIDS)/backend.pid
	@echo "Starting frontend (log: $(LOGS)/frontend.log)"
	$(NPM) --prefix frontend run dev \
	    > $(LOGS)/frontend.log 2>&1 & echo $$! > $(PIDS)/frontend.pid
	@echo ""
	@echo "  Backend  → http://localhost:8000"
	@echo "  Frontend → http://localhost:5173"
	@echo ""
	@echo "Run 'make logs' to tail output, 'make stop' to quit."

# ── production (built frontend served by FastAPI) ─────────────────────────────
start: _mkdirs build
	@$(MAKE) _stop-backend 2>/dev/null; true
	@echo "Starting backend (log: $(LOGS)/backend.log)"
	$(PYTHON) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 \
	    --timeout-graceful-shutdown 5 \
	    > $(LOGS)/backend.log 2>&1 & echo $$! > $(PIDS)/backend.pid
	@echo ""
	@echo "  App → http://localhost:8000"
	@echo ""
	@echo "Run 'make logs' to tail output, 'make stop' to quit."

# ── stop ──────────────────────────────────────────────────────────────────────
stop: _stop-backend _stop-frontend
	@echo "All processes stopped."

_stop-backend:
	@if [ -f $(PIDS)/backend.pid ]; then \
	    PID=$$(cat $(PIDS)/backend.pid); \
	    if kill -0 $$PID 2>/dev/null; then \
	        kill $$PID && echo "Stopped backend  (pid $$PID)"; \
	    fi; \
	    rm -f $(PIDS)/backend.pid; \
	fi

_stop-frontend:
	@if [ -f $(PIDS)/frontend.pid ]; then \
	    PID=$$(cat $(PIDS)/frontend.pid); \
	    if kill -0 $$PID 2>/dev/null; then \
	        pkill -P $$PID 2>/dev/null || true; \
	        kill $$PID 2>/dev/null && echo "Stopped frontend (pid $$PID)" || true; \
	    fi; \
	    rm -f $(PIDS)/frontend.pid; \
	fi
	@# Kill any orphaned vite/node processes from this project (ignore errors)
	@(VITE_PIDS=$$(pgrep -f "node_modules/.bin/vite" 2>/dev/null); \
	if [ -n "$$VITE_PIDS" ]; then \
	    kill $$VITE_PIDS 2>/dev/null && echo "Killed orphaned vite processes: $$VITE_PIDS"; \
	fi) || true
	@(ESBUILD_PIDS=$$(pgrep -f "@esbuild.*gemini" 2>/dev/null); \
	if [ -n "$$ESBUILD_PIDS" ]; then \
	    kill $$ESBUILD_PIDS 2>/dev/null && echo "Killed orphaned esbuild processes: $$ESBUILD_PIDS"; \
	fi) || true

# ── restart ───────────────────────────────────────────────────────────────────
restart: stop dev

# ── status ────────────────────────────────────────────────────────────────────
status:
	@echo "── backend ──────────────────────────"
	@if [ -f $(PIDS)/backend.pid ]; then \
	    PID=$$(cat $(PIDS)/backend.pid); \
	    if kill -0 $$PID 2>/dev/null; then \
	        echo "  running  (pid $$PID)"; \
	    else \
	        echo "  stopped  (stale pid $$PID)"; \
	    fi; \
	else \
	    echo "  stopped"; \
	fi
	@echo "── frontend ─────────────────────────"
	@if [ -f $(PIDS)/frontend.pid ]; then \
	    PID=$$(cat $(PIDS)/frontend.pid); \
	    if kill -0 $$PID 2>/dev/null; then \
	        echo "  running  (pid $$PID)"; \
	    else \
	        echo "  stopped  (stale pid $$PID)"; \
	    fi; \
	else \
	    echo "  stopped  (prod mode — served by backend)"; \
	fi

# ── logs ──────────────────────────────────────────────────────────────────────
logs:
	@mkdir -p $(LOGS)
	@touch $(LOGS)/backend.log $(LOGS)/frontend.log
	tail -f $(LOGS)/backend.log $(LOGS)/frontend.log

logs-backend:
	@mkdir -p $(LOGS)
	@touch $(LOGS)/backend.log
	tail -f $(LOGS)/backend.log

logs-frontend:
	@mkdir -p $(LOGS)
	@touch $(LOGS)/frontend.log
	tail -f $(LOGS)/frontend.log

# ── clean ─────────────────────────────────────────────────────────────────────
clean: stop
	rm -rf frontend/dist frontend/node_modules $(PIDS) $(LOGS)

# ── internal ──────────────────────────────────────────────────────────────────
_mkdirs:
	@mkdir -p $(PIDS) $(LOGS)
