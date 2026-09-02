# 👗 AI Stylist & Smart Wardrobe Manager
### Comprehensive System Architecture & Engineering Reference Guide

---

> [!NOTE]
> **Production Deployment Architecture**: The application is deployed on **Google Cloud Platform (Google Compute Engine)** for 24/7 high-availability bot routing and persistent storage, while leveraging **AWS Bedrock (Amazon Nova Pro / Claude 3.5)** as a high-performance multi-cloud AI inference backend. It combines multimodal vision intake, human-in-the-loop verification, conservative duplicate detection, interactive user profiling, and a LangGraph-driven contextual styling engine powered by live weather (Open-Meteo), real-time fashion web trends (DuckDuckGo `ddgs`), outfit history RAG, and deterministic style matrices.

---

## 📑 Table of Contents
1. [Methodology & Functional Overview](#1-methodology--functional-overview)
2. [Technical Architecture & Multi-Cloud Topology](#2-technical-architecture--multi-cloud-topology)
3. [LangGraph Stylist State Machine & RAG Flow](#3-langgraph-stylist-state-machine--rag-flow)
4. [Project Directory & File Purpose Map](#4-project-directory--file-purpose-map)
5. [Tech Stack & Dependencies](#5-tech-stack--dependencies)
6. [Database Architecture & Schema (SQLite)](#6-database-architecture--schema-sqlite)
7. [AI Models, Multimodal Prompts & Contracts](#7-ai-models-multimodal-prompts--contracts)
8. [Cloud Deployment & Production Setup (GCP + AWS)](#8-cloud-deployment--production-setup-gcp--aws)
9. [Execution & Zero-Friction Judge Evaluation Guide](#9-execution--zero-friction-judge-evaluation-guide)
10. [Automated Verification & Test Suite](#10-automated-verification--test-suite)
11. [VS Code to GitHub Upload Guide](#11-vs-code-to-github-upload-guide)

---

## 1. Methodology & Functional Overview

The system addresses the cognitive friction of everyday dressing by converting casual clothing photos into a structured digital inventory and generating personalized, context-aware outfit recommendations.

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

### Core Capabilities:

1. **Multimodal Vision Intake (`app/extractor.py`)**:
   - Detects whether an upload is a single clothing piece or a full **Outfit of the Day (OOTD)** containing multiple garments.
   - Extracts structured Pydantic schemas: category, sub-category, primary/accent colors, silhouette fit, fabric weight, formality tier ($1-5$), seasonality, and styling tags.
   - Overlays high-contrast, labeled visual badges directly onto photos in memory using Pillow.

2. **Human-in-the-Loop (HITL) Verification & Refinement (`app/handlers.py`)**:
   - Extracted items are staged in an unverified state (`is_verified = 0`).
   - Users can confirm with one tap or reply in plain language (e.g., *"the shirt is oversized navy linen, brand is Uniqlo"*), triggering an authoritative AI re-extraction.

3. **Conservative Duplicate Detection & Smart Linking (`app/extractor.py`)**:
   - Employs a dual-condition gate: candidates must match **both** garment category/silhouette and dominant color hue before checking visual perception hashing (64-bit pHash) and LLM confirmation.
   - Prevents duplicate entries while letting users link new photos as fresh appearances of existing wardrobe pieces without duplicating disk storage.

4. **Interactive Profile Onboarding (`app/profile_flow.py`)**:
   - 7-step Telegram conversation wizard capturing gender frame, height, weight, body build, proportions (e.g., long torso, broad shoulders), preferred silhouettes, avoided colors, and thermal preference (runs warm/cold).

5. **Contextual Agentic Stylist Graph (`app/stylist_graph.py`)**:
   - **Weather RAG**: Live Open-Meteo forecasts for real-time temperature, rain probability, and UV index.
   - **Web Search RAG**: DuckDuckGo (`ddgs`) trend scraping for occasion-specific and location-relevant dress codes.
   - **Outfit History RAG**: Vector/SQL lookups of past worn outfits for similar occasions.
   - **Anti-Repeat Wear Rotation**: Enforces a 48-hour cooldown on recently worn items.
   - **Deterministic Style Matrix (`app/style_matrix.py`)**: Enforces color theory (monochromatic, complementary, analogous) and silhouette balance (e.g., tight top + relaxed bottom).

6. **Compact Wardrobe & Laundry Management (`/wardrobe`, `/laundry`)**:
   - Clean 2-step menu displaying badged photo albums with **Edit**, **Delete**, and **Laundry** controls.
   - 4-word display limit prevents UI overflows while preserving 100% full, rich descriptions in SQLite for LLM reasoning.
   - Natural numeric sorting (`item_101`, `item_102`...) across all preview cards and buttons.

---

## 2. Technical Architecture & Multi-Cloud Topology

```mermaid
flowchart TD
    subgraph Telegram MTProto Layer
        User([User on Telegram Mobile / Desktop])
    end

    subgraph Google Cloud Platform [GCP Compute Engine Host]
        Bot[bot.py: Application Router]
        Handlers[app/handlers.py: Command & Callback Handlers]
        ProfileFlow[app/profile_flow.py: Profile Conversation]
        StylistGraph[app/stylist_graph.py: LangGraph Orchestrator]
        StyleMatrix[app/style_matrix.py: Deterministic Rules Engine]
        DB[(Persistent SQLite: data/wardrobe.db)]
        Images[(Persistent Disk: data/images/)]
    end

    subgraph Amazon Web Services [AWS Bedrock AI Backend]
        NovaVision[Amazon Nova Pro: Multimodal Garment Extraction]
        ClaudeStylist[Claude 3.5 / Nova: Stylist LLM Reasoning]
    end

    subgraph External RAG Providers
        OpenMeteo[Open-Meteo API: Live Hyper-Local Weather]
        DDGS[DuckDuckGo Search: Real-Time Fashion Trends]
    end

    User <-->|Telegram API| Bot
    Bot --> Handlers
    Bot --> ProfileFlow
    Handlers <--> StylistGraph
    Handlers <--> DB
    Handlers <--> Images
    ProfileFlow <--> DB
    
    StylistGraph --> OpenMeteo
    StylistGraph --> DDGS
    StylistGraph --> StyleMatrix
    StylistGraph <--> DB
    
    Handlers <-->|boto3 HTTPS| NovaVision
    StylistGraph <-->|boto3 HTTPS| ClaudeStylist
```

---

## 3. LangGraph Stylist State Machine & RAG Flow

The outfit generation engine is implemented as a stateful, cyclical **LangGraph state machine** in [`app/stylist_graph.py`](file:///Volumes/Jerry_SSD%201/Work/AI-work/Fashion-sense/app/stylist_graph.py).

```mermaid
flowchart TD
    Start([User sends /style occasion, location]) --> FetchNode[1. candidate_fetch_node]
    
    subgraph RAG Context Gathering
        FetchNode --> Weather[Open-Meteo API: Temp, Rain, UV]
        FetchNode --> Trends[DuckDuckGo ddgs: Live Trends]
        FetchNode --> History[SQL RAG: Past Outfits & Feedback]
        FetchNode --> Filter[SQL Filter: Clean items minus 48h Cooldown]
    end
    
    Weather --> RulesNode[2. trend_rules_node]
    Trends --> RulesNode
    History --> RulesNode
    Filter --> RulesNode
    
    subgraph Deterministic Rule Engine
        RulesNode --> ColorHarmony[Color Harmony Wheel: Monochrome/Analog/Split]
        RulesNode --> Proportions[Silhouette Balance: Fitted Top + Relaxed Bottom]
        RulesNode --> Thermal[Thermal Adjustment: User Warm/Cold Bias]
    end
    
    ColorHarmony --> LLMNode[3. stylist_llm_node]
    Proportions --> LLMNode
    Thermal --> LLMNode
    
    subgraph Multimodal LLM Reasoning
        LLMNode --> BedrockConverse[AWS Bedrock Nova Pro / Claude]
        BedrockConverse --> Assemble[Outfit Assembly & Style Rationale]
    end
    
    Assemble --> CritiqueNode[4. outfit_critique_node]
    
    subgraph Validation & Self-Critique Loop
        CritiqueNode -- Valid --> Output([Send Badged Outfit Photos & Actions])
        CritiqueNode -- Invalid / Missing Item --> Retry[Deterministic Fallback / Retry]
        Retry --> Output
    end
```

### Node Explanations:

| Node Name | Responsibilities |
| :--- | :--- |
| **`candidate_fetch_node`** | Gathers live weather forecasts via Open-Meteo, queries DuckDuckGo (`ddgs`) for occasion dress codes, pulls past user outfit records, and fetches verified inventory excluding items in laundry or worn within the last 48 hours. |
| **`trend_rules_node`** | Evaluates deterministic fashion rules from `app/style_matrix.py`: color harmony (monochromatic, complementary, analog), silhouette balance (fitted top + loose bottom), and user thermal offsets. |
| **`stylist_llm_node`** | Injects the full RAG context and candidate items into AWS Bedrock (Amazon Nova Pro / Claude 3.5) to synthesize a cohesive look with natural styling commentary. |
| **`outfit_critique_node`** | Validates that all recommended item IDs exist in clean inventory, checks formality consistency, and triggers an automatic deterministic fallback if LLM constraints fail. |

---

## 4. Project Directory & File Purpose Map

```
.
├── bot.py                  # Main entry point: registers handlers and starts Telegram polling
├── requirements.txt        # Production dependency specifications
├── .env.example            # Template for environment variables and secrets
├── .gitignore              # Protects secrets (.env) while tracking pre-seeded demo database
├── build_pdf.py            # Standalone generator for system_documentation.pdf
├── README.md               # Quick-start project documentation
├── system_documentation.md # Comprehensive engineering reference guide (this document)
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
│   ├── wardrobe.db         # Persistent SQLite database with pre-seeded demo items
│   └── images/             # Persistent clothing photo storage with demo wardrobe images
│
└── tests/
    ├── test_admin_pool.py          # Tests for Admin Test Pool mode & keyboard builders
    ├── test_batch_and_wardrobe.py  # Tests for batch intake & wardrobe CRUD
    ├── test_duplicate_detection.py # Tests for pHash & dual-condition duplicate linking
    └── test_intake_flow.py         # Tests for verification & plain-text corrections
```

---

## 5. Tech Stack & Dependencies

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Cloud Host** | Google Cloud (Compute Engine VM) | 24/7 host running the application runtime & persistent disk |
| **Language** | Python 3.10+ / 3.12 | Core asynchronous backend runtime |
| **Bot Framework** | `python-telegram-bot` (v21+) | Async Telegram Bot API client with ConversationHandlers |
| **LLM & Vision** | AWS Bedrock (`amazon.nova-pro-v1:0` / Claude 3.5) | Multimodal garment extraction and style reasoning |
| **Agentic Graph** | `langgraph` (v0.2+) | State graph orchestration, RAG coordination & self-critique |
| **Data Validation**| `pydantic` (v2.5+) | Type safety and strict JSON contract validation |
| **Database** | SQLite 3 (`sqlite3`) | Persistent relational database with auto-migrations |
| **Image Processing**| `Pillow` (PIL v10+) | Resizing, 64-bit perception hashing (pHash), image badges |
| **Web Search RAG** | `duckduckgo-search` / `ddgs` | Live dress code and occasion trend retrieval |
| **Weather API** | Open-Meteo API | Free, keyless live weather & forecast retrieval |

---

## 6. Database Architecture & Schema (SQLite)

The database at `data/wardrobe.db` uses a normalized relational schema with automatic forward migration:

```mermaid
erDiagram
    users ||--o{ garments : owns
    users ||--o{ wear_history : logs
    users ||--o{ user_outfits : saves
    users ||--o| user_profiles : configures
    garments ||--o{ garment_appearances : appears_in

    users {
        string user_id PK
        timestamp created_at
    }

    garments {
        string item_id PK
        string user_id FK
        string category
        string sub_category
        string color
        string accent_colors
        string silhouette_fit
        string fabric_weight
        int formality_tier
        string brand
        string style_tags
        int in_laundry
        int is_verified
        string image_path
        string phash
        timestamp created_at
    }

    garment_appearances {
        int id PK
        string item_id FK
        string capture_id
        string image_path
        timestamp created_at
    }

    wear_history {
        int id PK
        string user_id FK
        string item_ids
        string occasion
        string action
        timestamp created_at
    }

    user_profiles {
        string user_id PK
        string gender_presentation
        string height
        string weight
        string body_build
        string body_proportions
        string preferred_fits
        string style_archetypes
        string avoided_colors
        string thermal_preference
    }

    user_outfits {
        int id PK
        string user_id FK
        string item_ids
        string name
        string occasion
        timestamp created_at
    }
```

---

## 7. AI Models, Multimodal Prompts & Contracts

The system defines four specialized prompt roles with strict Pydantic JSON contracts:

| Prompt Role | Input Data | Output Contract | Responsibility |
| :--- | :--- | :--- | :--- |
| **Vision Cataloger** | JPEG image bytes + User Caption | `ExtractedGarment` JSON | Classifies single items vs OOTDs; extracts categories, colors, fit, fabric, formality tier, and brand. |
| **Identity Decider** | Two garment metadata dictionaries | `{"is_same_item": bool}` | Conservative identity verification before linking photo appearances. |
| **Correction Refiner** | Previous JSON + Plain Language Note | `GarmentExtractionResult` JSON | Applies authoritative user corrections (e.g., *"these pants are burgundy chinos"*) without hallucinating. |
| **Stylist Engine** | Wardrobe Inventory + Weather + Trends + History | `OutfitRecommendation` JSON | Synthesizes a complete outfit look matching occasion, weather, and proportions with styling reasoning. |

---

## 8. Cloud Deployment & Production Setup (GCP + AWS)

### 1. Google Cloud VM Deployment
The application runs 24/7 on a Google Compute Engine instance (`e2-small` or `e2-micro` Debian/Ubuntu):

```bash
# 1. Update system packages and install dependencies
sudo apt update && sudo apt install -y python3-pip python3-venv git tmux

# 2. Clone repository
git clone https://github.com/<your-username>/fashion-sense.git
cd fashion-sense

# 3. Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure .env credentials
nano .env
```

### 2. Multi-Cloud Environment Configuration (`.env`)
```env
# Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# AWS Bedrock AI Compute Credentials
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
# Set AWS_SESSION_TOKEN only if using temporary session keys (AWS SSO / Academy):
AWS_SESSION_TOKEN=
AWS_DEFAULT_REGION=us-east-1

# Persistence & Evaluation Settings
DATABASE_PATH=data/wardrobe.db
ADMIN_TEST_PASSWORD=demo123
```

### 3. Running 24/7 with `tmux`
```bash
# Launch tmux session
tmux new -s bot

# Start bot application
source .venv/bin/activate
python bot.py
```
*Detach from the session anytime with `Ctrl + B`, then `D`.*

---

## 9. Execution & Zero-Friction Judge Evaluation Guide

### Available Telegram Commands:

| Command | Description |
| :--- | :--- |
| `/start` | Welcome onboarding and interactive feature tour |
| `/profile` | 7-step onboarding wizard (build, proportions, thermal preference) |
| `/wardrobe` | Browse digital wardrobe with badged photos & 2-step management |
| `/style` | Trigger AI stylist (e.g., `/style date night`, `/style wedding in Tokyo`) |
| `/laundry` | View dirty clothes hamper and toggle items clean |
| `/delete` | Delete individual items or perform 2-step full wardrobe reset |
| `/admintest` | **Judge Mode**: Enter shared test pool with pre-seeded wardrobe items |
| `/adminlive` | Exit test pool and return to private user session |
| `/cancel` | Cancel any active conversation or prompt |

---

### 🧑‍⚖️ Zero-Friction Judge Evaluation (Admin Pool Mode)

To allow judges to test the styling capabilities immediately without having to photograph and upload 10+ items of clothing:

1. Send `/admintest` to the bot on Telegram.
2. Enter the demo password configured in `.env` (default: `demo123`).
3. You will enter **Shared Pool Mode** (`POOL_TEST_USER`).
4. Type `/wardrobe` to inspect the pre-seeded wardrobe, or type `/style dinner date in Tokyo` to evaluate the AI stylist immediately!
5. When finished, send `/adminlive` to switch back to your own private session.

---

## 10. Automated Verification & Test Suite

The test suite covers database persistence, vision extraction schemas, duplicate detection, natural sorting, and callback interactions:

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

## 11. VS Code to GitHub Upload Guide

Follow these steps to safely push this project to GitHub from Visual Studio Code without exposing your private `.env` secrets:

### 1. Verify `.gitignore` is Active
Ensure `.gitignore` contains `.env` and `.venv/`. Verify with:
```bash
git status
```
*(Make sure `.env` does NOT appear in the untracked files list).*

### 2. Initialize and Commit in VS Code
1. Open the **Source Control** tab in VS Code (`Ctrl+Shift+G` or `Cmd+Shift+G`).
2. Click **`+`** (Stage All Changes).
3. Type a commit message: `feat: multi-cloud architecture and pre-seeded demo wardrobe`.
4. Click **Commit** (or press `Cmd+Enter` / `Ctrl+Enter`).

### 3. Create a Remote Repository & Push
1. In VS Code, click **Publish Branch** (or **Publish to GitHub**).
2. Choose **Publish to GitHub private repository**.
3. Alternatively, via terminal:
   ```bash
   git branch -M main
   git remote add origin https://github.com/<your-username>/fashion-sense.git
   git push -u origin main
   ```

---

> [!TIP]
> **Multi-Cloud Talking Point for Judges**: The application decouples cloud hosting (Google Cloud for stateful bot workers and persistent database storage) from generative AI inference (AWS Bedrock for multimodal vision and Claude/Nova reasoning), achieving maximum architectural resilience and cost optimization.
