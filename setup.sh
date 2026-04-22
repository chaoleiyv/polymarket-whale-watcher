#!/usr/bin/env bash
# Polymarket Whale Watcher - One-Click Setup
# Usage: chmod +x setup.sh && ./setup.sh

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╭──────────────────────────────────────────────────────────╮"
echo "│           🐋 Polymarket Whale Watcher Setup              │"
echo "╰──────────────────────────────────────────────────────────╯"
echo -e "${NC}"

# ============================================================
# Step 1: Check Python
# ============================================================
echo -e "${CYAN}[1/4]${NC} Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is required but not installed.${NC}"
    echo "  Install from https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo -e "${RED}Error: Python 3.10+ required, found $PYTHON_VERSION${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Python $PYTHON_VERSION"

# ============================================================
# Step 2: Virtual environment
# ============================================================
echo -e "${CYAN}[2/4]${NC} Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "  ${GREEN}✓${NC} Virtual environment created"
else
    echo -e "  ${GREEN}✓${NC} Virtual environment already exists"
fi

# ============================================================
# Step 3: Install dependencies
# ============================================================
echo -e "${CYAN}[3/4]${NC} Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "  ${GREEN}✓${NC} Dependencies installed"

# Create data directories
mkdir -p data reports daily_briefings

# ============================================================
# Step 4: API Key Configuration
# ============================================================
echo -e "${CYAN}[4/4]${NC} Configuring API keys..."
echo ""

if [ -f ".env" ]; then
    echo -e "  ${YELLOW}Found existing .env file.${NC}"
    read -p "  Overwrite and reconfigure? [y/N] " overwrite
    if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
        echo -e "  ${GREEN}✓${NC} Keeping existing .env"
        echo ""
        echo -e "${GREEN}Setup complete! Run:${NC}"
        echo "  source venv/bin/activate"
        echo "  python -m src.main run"
        exit 0
    fi
fi

cp .env.example .env

# Helper function: prompt for a key and write to .env
set_key() {
    local var_name="$1"
    local prompt_text="$2"
    local url="$3"
    local required="$4"

    echo ""
    if [ "$required" = "required" ]; then
        echo -e "  ${BOLD}${var_name}${NC} ${RED}(required)${NC}"
    else
        echo -e "  ${BOLD}${var_name}${NC} ${DIM}(optional, press Enter to skip)${NC}"
    fi
    echo -e "  ${DIM}$prompt_text${NC}"
    echo -e "  ${DIM}→ $url${NC}"

    while true; do
        read -p "  Key: " key_value
        if [ -z "$key_value" ]; then
            if [ "$required" = "required" ]; then
                echo -e "  ${RED}This key is required. Please enter a value.${NC}"
                continue
            else
                echo -e "  ${DIM}Skipped${NC}"
                return
            fi
        fi
        break
    done

    # Replace the placeholder in .env
    if grep -q "^${var_name}=" .env; then
        sed -i.bak "s|^${var_name}=.*|${var_name}=${key_value}|" .env
    elif grep -q "^# ${var_name}=" .env; then
        sed -i.bak "s|^# ${var_name}=.*|${var_name}=${key_value}|" .env
    else
        echo "${var_name}=${key_value}" >> .env
    fi
    rm -f .env.bak
    echo -e "  ${GREEN}✓ Saved${NC}"
}

echo -e "${CYAN}╭──────────────────────────────────────────────────────────╮${NC}"
echo -e "${CYAN}│              API Key Configuration                      │${NC}"
echo -e "${CYAN}│                                                         │${NC}"
echo -e "${CYAN}│  The more keys you add, the better the analysis.        │${NC}"
echo -e "${CYAN}│  Only Gemini is required. Others are optional but       │${NC}"
echo -e "${CYAN}│  strongly recommended for full coverage.                │${NC}"
echo -e "${CYAN}╰──────────────────────────────────────────────────────────╯${NC}"

# --- Required ---
echo ""
echo -e "${YELLOW}━━━ Required (LLM Engine) ━━━${NC}"

set_key "GEMINI_API_KEY" \
    "Powers the LLM analysis (free tier available)" \
    "https://aistudio.google.com/apikey" \
    "required"

# --- Core Search ---
echo ""
echo -e "${YELLOW}━━━ Web Search (strongly recommended) ━━━${NC}"
echo -e "${DIM}  Without these, LLM falls back to DuckDuckGo (lower quality)${NC}"

set_key "TAVILY_API_KEY" \
    "Best web search quality, 1000 free searches/month" \
    "https://app.tavily.com/home"

set_key "SERPER_API_KEY" \
    "Google search fallback, 2500 free searches" \
    "https://serper.dev"

# --- Social Sentiment ---
echo ""
echo -e "${YELLOW}━━━ Social Sentiment ━━━${NC}"

set_key "TWITTER_API_KEY" \
    "Twitter/X sentiment & breaking news search" \
    "https://developer.x.com/en/portal/dashboard"

# --- Financial Data ---
echo ""
echo -e "${YELLOW}━━━ Financial & On-Chain Data ━━━${NC}"

set_key "POLYGON_API_KEY" \
    "Stock, ETF, forex, commodities data (free tier)" \
    "https://polygon.io/dashboard/signup"

set_key "FRED_API_KEY" \
    "US economic indicators (free, instant approval)" \
    "https://fred.stlouisfed.org/docs/api/api_key.html"

set_key "ETHERSCAN_API_KEY" \
    "On-chain wallet & contract analysis (free tier)" \
    "https://etherscan.io/myapikey"

set_key "CONGRESS_API_KEY" \
    "US legislation & bills tracking (free)" \
    "https://api.congress.gov/sign-up/"

# --- Summary ---
echo ""
echo ""

# Count configured keys
configured=0
total=8
for var in GEMINI_API_KEY TAVILY_API_KEY SERPER_API_KEY TWITTER_API_KEY POLYGON_API_KEY FRED_API_KEY ETHERSCAN_API_KEY CONGRESS_API_KEY; do
    val=$(grep "^${var}=" .env 2>/dev/null | cut -d= -f2-)
    if [ -n "$val" ] && [ "$val" != "your_gemini_api_key_here" ]; then
        configured=$((configured + 1))
    fi
done

echo -e "${GREEN}╭──────────────────────────────────────────────────────────╮${NC}"
echo -e "${GREEN}│              ✅ Setup Complete!                          │${NC}"
echo -e "${GREEN}│                                                         │${NC}"
echo -e "${GREEN}│  API Keys configured: ${configured}/${total}                             │${NC}"
echo -e "${GREEN}╰──────────────────────────────────────────────────────────╯${NC}"

if [ "$configured" -lt 4 ]; then
    echo ""
    echo -e "  ${YELLOW}Tip: More API keys = better analysis coverage.${NC}"
    echo -e "  ${YELLOW}You can edit .env anytime to add more keys later.${NC}"
fi

echo ""
echo "  Start monitoring:"
echo -e "    ${BOLD}source venv/bin/activate${NC}"
echo -e "    ${BOLD}python -m src.main run${NC}"
echo ""
echo "  Other commands:"
echo "    python -m src.main check-markets    # View trending markets"
echo "    python -m src.main dashboard        # Web dashboard"
echo "    python -m src.main briefing --today  # Daily briefing"
echo ""
