# 👗 AI Stylist & Smart Wardrobe Manager

> **An Agentic AI personal styling assistant delivered over Telegram.**
> Features multimodal vision intake, human-in-the-loop refinement, conservative duplicate detection, interactive user profiling, and a LangGraph-driven contextual styling engine powered by live weather, real-time web trends, outfit history RAG, and deterministic color/proportion matrices.

---

## 📑 Table of Contents
1. [Methodology & Functional Overview](#-1-methodology--functional-overview)
2. [Technical Architecture](#-2-technical-architecture)
3. [Project Directory & File Purpose Map](#-3-project-directory--file-purpose-map)
4. [Tech Stack & Dependencies](#-4-tech-stack--dependencies)
5. [Environment Setup & Installation](#-5-environment-setup--installation)
6. [Execution & Judge Evaluation Guide](#-6-execution--judge-evaluation-guide)
7. [Automated Test Suite](#-7-automated-test-suite)
8. [VS Code to GitHub Upload Guide](#-8-vs-code-to-github-upload-guide)

---

## 🌟 1. Methodology & Functional Overview

The system addresses the cognitive friction of everyday dressing by converting casual clothing photos into a digital inventory and generating personalized, context-aware outfit recommendations.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AGENTIC LIFECYCLE                              │
├──────────────────┬──────────────────┬──────────────────┬────────────────────┤
│ 1. PHOTO INTAKE  │ 2. HITL REVIEW   │ 3. WARDROBE MGT  │ 4. AGENTIC STYLIST │
│ • Single & OOTD  │ • Natural prompt │ • Clean 2-step   │ • Live Weather RAG │
│ • Multi-piece    │ • Dual-check dup │ • 4-word badges  │ • DuckDuckGo Trends│
│ • Bedrock Vision │ • Instant verify │ • Natural sort   │ • LangGraph Engine │
└──────────────────┴──────────────────┴──────────────────┴────────────────────┘
```

### Key Functional Capabilities:

1. **Multimodal Vision Intake (`app/extractor.py`)**:
   - Detects whether an image is a single clothing piece or a full **Outfit of the Day (OOTD)** containing multiple garments.
   - Extracts structured Pydantic schemas: category, sub-category, primary/accent colors, silhouette fit, fabric weight, formality tier ($1-5$), seasonality, and styling tags.
   - Labels photos in memory with high-contrast, labeled visual badges using Pillow.

2. **Human-in-the-Loop (HITL) Verification & Refinement (`app/handlers.py`)**:
   - Extracted items are staged in an unverified state (`is_verified = 0`).
   - Users can confirm with one tap or reply in plain language (e.g. *"the shirt is oversized navy linen, brand is Uniqlo"*), triggering an authoritative AI re-extraction.

3. **Conservative Duplicate Detection & Smart Linking (`app/extractor.py`)**:
   - Employs a dual-condition gate: candidates must match **both** garment category/silhouette and dominant color hue before checking visual perception hashing (pHash) and LLM confirmation.
   - Prevents duplicate entries while letting users link new photos as fresh appearances of existing wardrobe pieces.

4. **Interactive Profile Onboarding (`app/profile_flow.py`)**:
   - 7-step Telegram conversation handler capturing gender frame, height, weight, body build, proportions (e.g. long torso, broad shoulders), preferred silhouettes, and thermal preference (runs warm/cold).

5. **Contextual Agentic Stylist Graph (`app/stylist_graph.py`)**:
   - **Weather RAG**: Live Open-Meteo forecasts for real-time temperature, rain probability, UV index, and apparent temperature.
   - **Web Search RAG**: DuckDuckGo (`ddgs`) trend scraping for occasion-specific and location-relevant dress codes.
   - **Outfit History RAG**: Vector/SQL lookups of past worn outfits for similar occasions.
   - **Anti-Repeat Wear Rotation**: Enforces a 48-hour cooldown on recently worn items.
   - **Deterministic Style Matrix (`app/style_matrix.py`)**: Enforces color theory (monochromatic, complementary, analog) and silhouette balance (e.g. tight top + relaxed bottom).

6. **Compact Wardrobe & Laundry Management (`/wardrobe`, `/laundry`)**:
   - Clean 2-step menu displaying badged photo albums with **Edit**, **Delete**, and **Laundry** controls.
   - 4-word display limit prevents UI overflows while preserving full, rich descriptions in SQLite for LLM reasoning.
   - Natural numeric sorting (`item_101`, `item_102`...) across all preview cards and buttons.

---

## 🏗️ 2. Technical Architecture

```mermaid
flowchart TD
    subgraph Telegram Client
        User([User on Telegram])
    end

    subgraph Bot Application Layer
        Bot[bot.py: Application Router]
        Handlers[app/handlers.py: Command & Callback Handlers]
        ProfileFlow[app/profile_flow.py: Profile Conversation]
    end

    subgraph Agentic AI Core
        Extractor[app/extractor.py: Multimodal Vision Extractor]
        StylistGraph[app/stylist_graph.py: LangGraph Orchestrator]
        StyleMatrix[app/style_matrix.py: Deterministic Rules Engine]
    end

    subgraph External Services & RAG
        Bedrock[AWS Bedrock: Amazon Nova Pro / Claude]
        WeatherAPI[Open-Meteo API: Live Weather]
        DDGS[DuckDuckGo Search: Live Fashion Trends]
    end

    subgraph Storage Layer
        DB[(SQLite: data/wardrobe.db)]
        Images[(Local Storage: data/images/)]
    end

    User <-->|Telegram MTProto API| Bot
    Bot --> Handlers
    Bot --> ProfileFlow
    Handlers <--> Extractor
    Handlers <--> StylistGraph
    Handlers <--> DB
    Handlers <--> Images
    ProfileFlow <--> DB
    
    Extractor <-->|Vision Inference| Bedrock
    StylistGraph --> WeatherAPI
    StylistGraph --> DDGS
    StylistGraph --> StyleMatrix
    StylistGraph <--> DB
    StylistGraph <-->|Converse API| Bedrock
```

### Stylist Graph Node Flow:

```
[Start] ──> [candidate_fetch_node] (Weather RAG + Web Trends + History)
                 │
                 ▼
            [trend_rules_node] (Deterministic Matrix + Thermal Adjustment)
                 │
                 ▼
            [stylist_llm_node] (Bedrock Reasoning + Outfit Assembly)
                 │
                 ▼
            [outfit_critique_node] (Rule Validation & Constraint Checks)
                 │
                 ├── (Pass) ──> [End: Structured Recommendation]
                 └── (Fail) ──> [Retry / Adjust Candidates]
```

---

## 📂 3. Project Directory & File Purpose Map

```
.
├── bot.py                  # Main entry point: registers handlers and starts Telegram polling
├── requirements.txt        # Production dependency specifications
├── .env.example            # Template for environment variables and secrets
├── .gitignore              # Protects secrets (.env), local database, and user photos
├── build_pdf.py            # Standalone generator for system_documentation.pdf
├── README.md               # Project documentation
│
├── app/
│   ├── __init__.py         # Package initialization
│   ├── config.py           # Environment loader & Settings dataclass (.env parsing)
│   ├── database.py         # SQLite schema, migrations, CRUD helpers, and wear history
│   ├── extractor.py        # AWS Bedrock multimodal vision extraction, pHash & badges
│   ├── handlers.py         # Main Telegram command handlers, intake & wardrobe callbacks
│   ├── models.py           # Pydantic schemas (Garment, UserProfile, Recommendation)
│   ├── paths.py            # Path resolver for database, storage, and image assets
│   ├── profile_flow.py     # 7-step interactive /profile setup ConversationHandler
│   ├── style_matrix.py     # Deterministic color theory, proportion & formality rules
│   ├── stylist_graph.py    # LangGraph state machine orchestrating AI stylist workflow
│   ├── weather.py          # Open-Meteo client for live temperature, rain & UV data
│   └── web_search.py       # DuckDuckGo (ddgs) trend retriever for occasion context
│
├── data/
│   ├── wardrobe.db         # Local SQLite database (created automatically)
│   └── images/             # Stored user clothing photos (created automatically)
│
└── tests/
    ├── test_admin_pool.py          # Tests for Admin Test Pool mode & keyboard builders
    ├── test_batch_and_wardrobe.py  # Tests for batch intake & wardrobe CRUD
    ├── test_duplicate_detection.py # Tests for pHash & dual-condition duplicate linking
    └── test_intake_flow.py         # Tests for verification & plain-text corrections
```

---

## 💻 4. Tech Stack & Dependencies

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Language** | Python 3.10+ / 3.12 | Core backend language |
| **Bot Framework** | `python-telegram-bot` (v21+) | Async Telegram Bot API client |
| **LLM & Vision** | AWS Bedrock (`amazon.nova-pro-v1:0` / Claude 3.5) | Multimodal extraction & style reasoning |
| **Agentic Graph** | `langgraph` (v0.2+) | State graph orchestration & self-critique loop |
| **Data Validation**| `pydantic` (v2.5+) | Type safety and JSON schema validation |
| **Database** | SQLite 3 (`sqlite3`) | Lightweight local relational persistence |
| **Image Processing**| `Pillow` (PIL v10+) | Resizing, pHash perception hashing, image badges |
| **Web Search RAG** | `duckduckgo-search` / `ddgs` | Live dress code and style trend retrieval |
| **Weather API** | Open-Meteo API | Free, keyless live weather & forecast retrieval |

---

## ⚙️ 5. Environment Setup & Installation

### Step 1: Clone the Repository
```bash
git clone https://github.com/your-username/fashion-sense.git
cd fashion-sense
```

### Step 2: Create and Activate Virtual Environment
```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (Command Prompt / PowerShell)
python -m venv .venv
.venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: Configure Secrets (`.env`)
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

Edit `.env`:
```env
# Required: Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ

# Required: AWS Bedrock Credentials
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
# Set AWS_SESSION_TOKEN only if using temporary session keys (AWS SSO / Academy):
AWS_SESSION_TOKEN=
AWS_DEFAULT_REGION=us-east-1

# Optional Settings
DATABASE_PATH=data/wardrobe.db
ADMIN_TEST_PASSWORD=demo123
```

---

## 🚀 6. Execution & Judge Evaluation Guide

### Starting the Bot:
```bash
python bot.py
```
*The bot runs gracefully with live terminal logging. Stop anytime with `Ctrl+C`.*

### Available Telegram Commands:

| Command | Description |
| :--- | :--- |
| `/start` | Welcome message and interactive quick-start guide |
| `/profile` | Interactive 7-step onboarding (body build, proportions, thermal preference) |
| `/wardrobe` | Browse digital wardrobe with badged photos & 2-step management |
| `/style` | Trigger AI stylist (e.g. `/style date night`, `/style wedding in Tokyo`) |
| `/laundry` | Check dirty clothes hamper and mark items clean |
| `/delete` | Remove items or reset wardrobe |
| `/admintest` | **Judge Mode**: Enter shared test pool with pre-seeded wardrobe items |
| `/adminlive` | Exit test pool and return to private user wardrobe |
| `/cancel` | Cancel any ongoing operation or edit |

---

### 🧑‍⚖️ Zero-Friction Judge Evaluation (Admin Pool Mode)

To allow judges to test the styling capabilities immediately without having to photograph and upload 10+ items of clothing:

1. Send `/admintest` to the bot on Telegram.
2. Enter the demo password configured in `.env` (default: `demo123`).
3. You will enter **Shared Pool Mode** (`POOL_TEST_USER`).
4. Type `/wardrobe` to inspect the pre-seeded wardrobe, or type `/style dinner date` to evaluate the AI stylist immediately!
5. When finished, send `/adminlive` to switch back to your own private session.

---

## 🧪 7. Automated Test Suite

The repository includes a comprehensive unit test suite covering database operations, vision parsing, duplicate detection, natural sorting, and callback interactions:

```bash
# Run all unit tests:
python -m unittest discover -s . -p "test_*.py"
```

### Test Files Overview:
- `test_admin_pool.py`: Verifies admin pool mode isolation, markdown escaping, and wardrobe keyboards.
- `test_batch_and_wardrobe.py`: Verifies multi-photo batch intake, 4-word title caps, and category views.
- `test_duplicate_detection.py`: Verifies pHash calculations and dual-condition duplicate matching.
- `test_intake_flow.py`: Verifies HITL verification and single-item edit confirmations.

---

## 📤 8. VS Code to GitHub Upload Guide

Follow these steps to safely push this project to GitHub from Visual Studio Code without exposing your private `.env` secrets or database:

### 1. Verify `.gitignore` is Active
Ensure `.gitignore` contains `.env`, `data/*.db`, and `data/images/*`. Verify with:
```bash
git status
```
*(Make sure `.env` and `data/wardrobe.db` do NOT appear in the untracked files list).*

### 2. Initialize and Commit in VS Code
1. Open the **Source Control** tab in VS Code (`Ctrl+Shift+G` or `Cmd+Shift+G`).
2. If not initialized, click **Initialize Repository**.
3. Stage all files by clicking the **+** icon (Stage All Changes).
4. Type a commit message (e.g. `feat: initial commit of AI Stylist bot`).
5. Click **Commit** (or press `Cmd+Enter` / `Ctrl+Enter`).

### 3. Create a Remote Repository & Push
1. In VS Code, click **Publish Branch** (or **Publish to GitHub**).
2. Choose **Publish to GitHub private repository** or **public repository**.
3. Alternatively, via terminal:
   ```bash
   git remote add origin https://github.com/<your-username>/fashion-sense.git
   git branch -M main
   git push -u origin main
   ```

---

## 📄 License
Developed for the AI Agent Hackathon. Distributed under the MIT License.
