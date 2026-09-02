#!/usr/bin/env python3
"""Generate a comprehensive, professional PDF documentation for the AI Stylist Bot."""
import os
import sys

def create_pdf(output_path: str):
    objects = []
    
    def add_object(content: str) -> int:
        objects.append(content)
        return len(objects)

    class PageBuilder:
        def __init__(self, page_num: int):
            self.page_num = page_num
            self.stream_lines = []
            self.width = 595.28  # A4
            self.height = 841.89 # A4
            self.margin_x = 42.0
            self.margin_top = 40.0
            self.margin_bottom = 40.0
            self.y = self.height - self.margin_top
            
            # Running header line
            self.stream_lines.append("0.75 0.75 0.78 RG 0.5 w")
            self.stream_lines.append(f"{self.margin_x} {self.height - 30} m {self.width - self.margin_x} {self.height - 30} l S")
            # Header text
            self.stream_lines.append("BT /F1 7.5 Tf 0.45 0.45 0.50 rg")
            self.stream_lines.append(f"{self.margin_x} {self.height - 25} Td (AI Stylist & Smart Wardrobe Manager -- Engineering System Documentation) Tj ET")

        def finalize_footer(self, total_pages: int):
            # Running footer line
            self.stream_lines.append(f"0.75 0.75 0.78 RG 0.5 w")
            self.stream_lines.append(f"{self.margin_x} 36 m {self.width - self.margin_x} 36 l S")
            # Footer text
            self.stream_lines.append(f"BT /F1 7.5 Tf 0.45 0.45 0.50 rg")
            self.stream_lines.append(f"{self.width - self.margin_x - 55} 25 Td (Page {self.page_num} of {total_pages}) Tj ET")
            self.stream_lines.append(f"BT /F1 7.5 Tf 0.45 0.45 0.50 rg")
            self.stream_lines.append(f"{self.margin_x} 25 Td (Judge Reference Guide -- Agentic AI Architecture) Tj ET")

        def add_title(self, text: str, subtitle: str = ""):
            self.y -= 8
            self.stream_lines.append("BT /F2 15 Tf 0.10 0.15 0.25 rg")
            self.stream_lines.append(f"{self.margin_x} {self.y} Td ({self._escape(text)}) Tj ET")
            self.y -= 15
            if subtitle:
                self.stream_lines.append("BT /F1 8.5 Tf 0.35 0.40 0.48 rg")
                self.stream_lines.append(f"{self.margin_x} {self.y} Td ({self._escape(subtitle)}) Tj ET")
                self.y -= 13
            self.y -= 4
            # Accent divider bar
            self.stream_lines.append("0.18 0.42 0.82 rg")
            self.stream_lines.append(f"{self.margin_x} {self.y + 2} {self.width - 2 * self.margin_x} 2.0 re f")
            self.y -= 12

        def add_heading1(self, text: str):
            if self.y < 110:
                return False
            self.y -= 10
            self.stream_lines.append("BT /F2 11 Tf 0.12 0.20 0.40 rg")
            self.stream_lines.append(f"{self.margin_x} {self.y} Td ({self._escape(text)}) Tj ET")
            self.y -= 4
            # subtle underline
            self.stream_lines.append("0.85 0.88 0.92 RG 0.8 w")
            self.stream_lines.append(f"{self.margin_x} {self.y} m {self.width - self.margin_x} {self.y} l S")
            self.y -= 9
            return True

        def add_heading2(self, text: str):
            if self.y < 90:
                return False
            self.y -= 6
            self.stream_lines.append("BT /F2 9.5 Tf 0.18 0.28 0.45 rg")
            self.stream_lines.append(f"{self.margin_x} {self.y} Td ({self._escape(text)}) Tj ET")
            self.y -= 8
            return True

        def add_paragraph(self, text: str, font="F1", size=8.0, leading=10.5, color=(0.15, 0.15, 0.18)):
            words = text.split(" ")
            lines = []
            curr = []
            max_chars = int((self.width - 2 * self.margin_x) / (size * 0.52))
            for w in words:
                curr_len = sum(len(x) + 1 for x in curr) + len(w)
                if curr_len > max_chars:
                    lines.append(" ".join(curr))
                    curr = [w]
                else:
                    curr.append(w)
            if curr:
                lines.append(" ".join(curr))

            needed_height = len(lines) * leading
            if self.y - needed_height < self.margin_bottom + 10:
                return False

            r, g, b = color
            self.stream_lines.append(f"BT /{font} {size} Tf {r:.2f} {g:.2f} {b:.2f} rg")
            self.stream_lines.append(f"{self.margin_x} {self.y} Td")
            for i, line in enumerate(lines):
                if i > 0:
                    self.stream_lines.append(f"0 -{leading} Td")
                self.stream_lines.append(f"({self._escape(line)}) Tj")
            self.stream_lines.append("ET")
            self.y -= needed_height + 3
            return True

        def add_bullet(self, title: str, body: str, leading=10.2):
            prefix = f"{title}: " if title else ""
            full_text = f"{prefix}{body}"
            max_chars = int((self.width - 2 * self.margin_x - 12) / (7.6 * 0.52))
            words = full_text.split(" ")
            lines = []
            curr = []
            for w in words:
                curr_len = sum(len(x) + 1 for x in curr) + len(w)
                if curr_len > max_chars:
                    lines.append(" ".join(curr))
                    curr = [w]
                else:
                    curr.append(w)
            if curr:
                lines.append(" ".join(curr))

            needed_height = len(lines) * leading
            if self.y - needed_height < self.margin_bottom + 10:
                return False

            # Vector bullet dot
            dot_y = self.y + 2.0
            self.stream_lines.append("0.18 0.42 0.82 rg")
            self.stream_lines.append(f"{self.margin_x + 2} {dot_y} 2.5 2.5 re f")

            self.stream_lines.append(f"BT /F1 7.6 Tf 0.18 0.18 0.22 rg")
            self.stream_lines.append(f"{self.margin_x + 9} {self.y} Td")
            for i, line in enumerate(lines):
                if i > 0:
                    self.stream_lines.append(f"0 -{leading} Td")
                self.stream_lines.append(f"({self._escape(line)}) Tj")
            self.stream_lines.append("ET")
            self.y -= needed_height + 2.0
            return True

        def add_code_box(self, code_lines: list[str], font="F3", size=6.5, leading=8.0):
            needed_height = len(code_lines) * leading + 10
            if self.y - needed_height < self.margin_bottom + 10:
                return False

            box_x = self.margin_x
            box_y = self.y - needed_height
            box_w = self.width - 2 * self.margin_x
            box_h = needed_height

            self.stream_lines.append("0.96 0.97 0.98 rg")
            self.stream_lines.append(f"{box_x} {box_y} {box_w} {box_h} re f")
            self.stream_lines.append("0.82 0.85 0.88 RG 0.5 w")
            self.stream_lines.append(f"{box_x} {box_y} {box_w} {box_h} re s")

            text_y = self.y - 8
            self.stream_lines.append(f"BT /{font} {size} Tf 0.12 0.15 0.20 rg")
            self.stream_lines.append(f"{box_x + 6} {text_y} Td")
            for i, line in enumerate(code_lines):
                if i > 0:
                    self.stream_lines.append(f"0 -{leading} Td")
                self.stream_lines.append(f"({self._escape(line)}) Tj")
            self.stream_lines.append("ET")

            self.y -= needed_height + 5
            return True

        def add_table(self, headers: list[str], rows: list[list[str]], col_widths: list[float], font_size=7.0):
            row_h = 13.5
            total_h = (len(rows) + 1) * row_h
            if self.y - total_h < self.margin_bottom + 10:
                return False

            table_x = self.margin_x
            cur_y = self.y

            self.stream_lines.append("0.18 0.32 0.52 rg")
            self.stream_lines.append(f"{table_x} {cur_y - row_h} {sum(col_widths)} {row_h} re f")

            cur_x = table_x
            for idx, h in enumerate(headers):
                self.stream_lines.append(f"BT /F2 {font_size + 0.3} Tf 1.0 1.0 1.0 rg")
                self.stream_lines.append(f"{cur_x + 3} {cur_y - 9.5} Td ({self._escape(h)}) Tj ET")
                cur_x += col_widths[idx]

            cur_y -= row_h

            for r_idx, r in enumerate(rows):
                if r_idx % 2 == 1:
                    self.stream_lines.append("0.97 0.98 0.99 rg")
                    self.stream_lines.append(f"{table_x} {cur_y - row_h} {sum(col_widths)} {row_h} re f")

                cur_x = table_x
                for c_idx, val in enumerate(r):
                    self.stream_lines.append(f"BT /F1 {font_size} Tf 0.15 0.15 0.18 rg")
                    self.stream_lines.append(f"{cur_x + 3} {cur_y - 9.5} Td ({self._escape(val)}) Tj ET")
                    cur_x += col_widths[c_idx]
                cur_y -= row_h

            self.stream_lines.append("0.80 0.83 0.88 RG 0.5 w")
            self.stream_lines.append(f"{table_x} {cur_y} {sum(col_widths)} {total_h} re s")

            self.y = cur_y - 6
            return True

        def _escape(self, s: str) -> str:
            s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            s = s.replace("—", "--").replace("–", "-").replace("→", "->")
            s = s.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
            s = s.replace("•", "")
            return s.encode("latin1", "replace").decode("latin1")

        def get_stream(self) -> str:
            return "\n".join(self.stream_lines)

    # -------------------------------------------------------------
    # PAGES SETUP
    # -------------------------------------------------------------
    all_pages = []
    
    # PAGE 1: Methodology & Functional Overview
    p1 = PageBuilder(1)
    p1.add_title(
        "AI Stylist & Wardrobe Manager -- Engineering System Documentation",
        "Multimodal Vision Intake, HITL Refinement, LangGraph Stylist Engine & Telegram Client"
    )
    p1.add_heading1("1. Methodology & Functional Overview")
    p1.add_paragraph(
        "The system eliminates the cognitive fatigue of daily outfit selection by transforming unstructured casual wardrobe "
        "photos into a structured digital inventory, combining live contextual retrieval (Weather RAG, Web Trend scraping, "
        "User History RAG) with a self-critiquing LangGraph agentic loop to generate personalized outfit recommendations."
    )
    
    p1.add_heading2("Core Functional Capabilities:")
    p1.add_bullet("Multimodal Vision Intake (app/extractor.py)", "Automatically detects single-garment items or full multi-piece Outfits of the Day (OOTD). Extracts structured Pydantic schemas: category, sub-category, primary/accent colors, silhouette fit, fabric weight, formality tier (1-5), and styling tags.")
    p1.add_bullet("Human-In-The-Loop (HITL) Verification (app/handlers.py)", "Extracted items are staged in an unconfirmed state (is_verified=0). Users verify with 1-tap or refine attributes in conversational language (e.g. 'the shirt is navy linen, brand is Uniqlo'), triggering authoritative re-extraction.")
    p1.add_bullet("Conservative Dual-Condition Duplicate Prevention", "Imposes a strict gate: candidates must match both category and primary color hue before perceptual hashing (64-bit pHash) and Bedrock LLM identity checks can link them as appearances of existing items.")
    p1.add_bullet("Interactive Profile Onboarding (app/profile_flow.py)", "7-step Telegram wizard capturing body build, proportions (e.g. long torso, broad shoulders), preferred silhouettes, avoided colors, and thermal preference (runs warm/cold).")
    p1.add_bullet("Contextual Agentic Stylist Graph (app/stylist_graph.py)", "Executes an asynchronous LangGraph pipeline integrating live Open-Meteo weather forecasts, DuckDuckGo (ddgs) web fashion trends, past OOTD history RAG, and a 48h anti-repeat wear cooldown.")
    p1.add_bullet("Compact 2-Step Wardrobe & Laundry Management", "Organized photo album views with in-place Edit, Delete, and Laundry actions. Word-capped preview badges (4 words) ensure zero UI overflow while SQLite retains full rich descriptions for LLM reasoning.")
    
    p1.add_heading1("2. High-Level System Topology")
    arch_ascii = [
        "                      +-----------------------------------------+",
        "                      |         Telegram Client UI (User)       |",
        "                      |  (Photos, Captions, Interactive Inline) |",
        "                      +--------------------+--------------------+",
        "                                           | MTProto Async",
        "                                           v",
        "                      +--------------------+--------------------+",
        "                      |       Bot Application Router (bot.py)   |",
        "                      |  (Debounce, Commands, Callback Queries) |",
        "                      +----------+-------------------+----------+",
        "                                 |                   |",
        "         [Intake Flow]           |                   | [Stylist Graph Flow]",
        "                                 v                   v",
        "           +-----------------------------+   +-----------------------------+",
        "           | Bedrock Multimodal Vision   |   | LangGraph Stylist Engine    |",
        "           | (Nova Pro / Claude 3.5)     |   | (RAG Orchestrator + Node FSM|",
        "           +--------------+--------------+   +--------------+--------------+",
        "                          |                                 |",
        "                          v                                 v",
        "           +-----------------------------+   +-----------------------------+",
        "           | Dual-Condition Deduplication|   | External Context & RAG      |",
        "           | (pHash + Hue + LLM Link)    |   | (Open-Meteo API + DDGS RAG) |",
        "           +--------------+--------------+   +--------------+--------------+",
        "                          |                                 |",
        "                          +----------------+----------------+",
        "                                           |",
        "                                           v",
        "                      +--------------------+--------------------+",
        "                      |       SQLite Database & Local Storage   |",
        "                      | (data/wardrobe.db & data/images/)       |",
        "                      +-----------------------------------------+",
    ]
    p1.add_code_box(arch_ascii, font="F3", size=6.3, leading=7.5)
    all_pages.append(p1)

    # PAGE 2: LangGraph Stylist Graph & Technical Architecture
    p2 = PageBuilder(2)
    p2.add_heading1("3. LangGraph Stylist Architecture & State Machine")
    p2.add_paragraph(
        "The outfit generation engine is implemented as a stateful, cyclical LangGraph state machine (app/stylist_graph.py). "
        "It decouples context gathering, deterministic constraint filtering, LLM reasoning, and validation critique into modular nodes:"
    )

    node_ascii = [
        "        [Start /style] ",
        "               │",
        "               ▼",
        "    +───────────────────────+   -> 1. Open-Meteo Live Forecast (Temp, Rain, UV)",
        "    | candidate_fetch_node  |   -> 2. DuckDuckGo (ddgs) Live Trend Retrieval",
        "    | (Context & RAG Fetch) |   -> 3. Past Outfit History RAG (User Preferences)",
        "    +──────────┬────────────+   -> 4. SQL Filter: Clean inventory minus 48h Worn",
        "               │",
        "               ▼",
        "    +───────────────────────+   -> 1. Color Harmony Rules (Monochrome, Analog, Split)",
        "    |   trend_rules_node    |   -> 2. Silhouette Balance (Tight Top + Relaxed Bottom)",
        "    | (Deterministic Rules) |   -> 3. Thermal Offset (User warm/cold vs Forecast)",
        "    +──────────┬────────────+   ",
        "               │",
        "               ▼",
        "    +───────────────────────+   -> 1. Amazon Nova Pro / Claude Converse API",
        "    |   stylist_llm_node    |   -> 2. Assembles candidate item IDs with styling rationale",
        "    | (Multimodal Reasoning)|   -> 3. Formats strict JSON contract",
        "    +──────────┬────────────+   ",
        "               │",
        "               ▼",
        "    +───────────────────────+   (Pass) ──> [Send Outfit Photos & Action Buttons]",
        "    | outfit_critique_node  |",
        "    |  (Validation Loop)    |   (Fail) ──> [Re-seed Candidates / Fallback Rules]",
        "    +───────────────────────+   ",
    ]
    p2.add_code_box(node_ascii, font="F3", size=6.3, leading=7.6)

    p2.add_heading2("Detailed LangGraph Node Responsibilities:")
    p2.add_bullet("1. candidate_fetch_node", "Queries Open-Meteo REST API for live weather, executes DuckDuckGo (ddgs) search for dress codes, pulls user outfit history, and retrieves clean inventory excluding garments in laundry or worn within the last 48 hours.")
    p2.add_bullet("2. trend_rules_node", "Evaluates deterministic rules from app/style_matrix.py. Checks color compatibility, computes silhouette ratios, and applies thermal offsets based on user profile preferences.")
    p2.add_bullet("3. stylist_llm_node", "Prompts AWS Bedrock Converse API with prompt-injected RAG context, profile constraints, and available items to assemble the optimal multi-piece combination.")
    p2.add_bullet("4. outfit_critique_node", "Validates that all recommended item IDs exist in clean inventory, checks formality consistency, and triggers an automatic deterministic fallback if LLM constraints fail.")
    all_pages.append(p2)

    # PAGE 3: Codebase Structure & Tech Stack
    p3 = PageBuilder(3)
    p3.add_heading1("4. Codebase Structure & Module Map")
    p3.add_paragraph("The repository is architected with strict separation of concerns, single-responsibility modules, and comprehensive error boundaries:")
    
    files_headers = ["File / Module", "Layer", "Responsibilities & Architectural Role"]
    files_rows = [
        ["bot.py", "Entrypoint", "Initializes DB, configures PTB Application, registers handlers, starts async polling."],
        ["app/config.py", "Config", "Loads .env credentials, parses Settings dataclass, validates AWS & Telegram keys."],
        ["app/database.py", "Persistence", "SQLite connection manager, schema migrations, CRUD queries, wear history logging."],
        ["app/extractor.py", "Vision / AI", "AWS Bedrock Converse API client, vision extraction prompts, 64-bit pHash, badge renderer."],
        ["app/handlers.py", "Controller", "Telegram commands (/wardrobe, /style, /laundry, /delete), intake debouncing, callbacks."],
        ["app/models.py", "Contracts", "Pydantic v2 schemas: ExtractedGarment, UserProfile, OutfitRecommendation."],
        ["app/paths.py", "Filesystem", "Resolves persistent data/ directories, SQLite DB paths, and image storage paths."],
        ["app/profile_flow.py", "Wizard", "7-step interactive /profile ConversationHandler capturing body frame & preferences."],
        ["app/style_matrix.py", "Rule Engine", "Deterministic color harmony wheel, silhouette proportion rules, offline fallback stylist."],
        ["app/stylist_graph.py", "Agent Core", "Stateful LangGraph orchestrator executing RAG context -> LLM stylist -> Critique loop."],
        ["app/weather.py", "RAG Tool", "Open-Meteo REST client retrieving real-time temperature, precipitation, and UV index."],
        ["app/web_search.py", "RAG Tool", "DuckDuckGo (ddgs) client retrieving live occasion-specific fashion trend snippets."],
        ["build_pdf.py", "Docs Utility", "Compiles comprehensive system_documentation.pdf engineering reference guide."],
        ["test_*.py", "Verification", "Automated test suite (intake, duplicate prevention, wardrobe CRUD, admin pool mode)."],
    ]
    p3.add_table(files_headers, files_rows, [85.0, 65.0, 360.0], font_size=6.6)

    p3.add_heading1("5. Tech Stack & Dependencies")
    stack_headers = ["Layer", "Technology", "Version", "Purpose"]
    stack_rows = [
        ["Language", "Python", ">= 3.10", "Core asynchronous runtime"],
        ["Bot Framework", "python-telegram-bot", ">= 21.0", "Async MTProto Telegram API wrapper"],
        ["LLM / Vision", "AWS Bedrock (Nova / Claude)", "Converse API", "Multimodal extraction & style reasoning"],
        ["Agent Orchestration", "LangGraph", ">= 0.2.0", "State machine with RAG & self-critique loop"],
        ["Data Contracts", "Pydantic", ">= 2.5.0", "Strict JSON schema validation and type safety"],
        ["Database", "SQLite 3", "Built-in", "Local relational storage with auto-migrations"],
        ["Image Processing", "Pillow (PIL)", ">= 10.0.0", "Resizing, 64-bit pHash, and labeled image badges"],
        ["Web Trends RAG", "duckduckgo-search / ddgs", ">= 6.0.0", "Live occasion & location fashion trend retrieval"],
        ["Weather API", "Open-Meteo API", "REST v1", "Keyless real-time weather & temperature forecasting"],
    ]
    p3.add_table(stack_headers, stack_rows, [85.0, 130.0, 75.0, 220.0], font_size=6.6)
    all_pages.append(p3)

    # PAGE 4: Database Architecture & Prompts
    p4 = PageBuilder(4)
    p4.add_heading1("6. Database Architecture (SQLite)")
    p4.add_paragraph("The SQLite schema (data/wardrobe.db) provides normalized relational persistence with automatic column migration:")
    p4.add_bullet("garments", "Stores individual clothing items: item_id, user_id, category, sub_category, color, accent_colors, silhouette_fit, fabric_weight, formality_tier, brand, style_tags, in_laundry, is_verified, created_at.")
    p4.add_bullet("garment_appearances", "Tracks all photo appearances of an item, enabling multiple pictures (intake photo, OOTD snaps) to link to a single physical piece without duplicating storage.")
    p4.add_bullet("wear_history", "Logs outfit wear events, timestamps, and occasions. Powering the 48-hour anti-repeat rotation cooldown and rejected-outfit blacklisting.")
    p4.add_bullet("user_profiles", "Stores user gender frame, height, weight, body build, proportions (e.g. broad shoulders), preferred fits, and thermal preference (warm/cold).")
    p4.add_bullet("user_outfits", "Saves verified favorite outfit combinations for user reference and style similarity RAG.")

    p4.add_heading1("7. AI Models, Prompts & Contracts")
    p4.add_paragraph("The system defines strict JSON contracts across distinct prompt roles to guarantee schema compliance:")
    
    prompt_headers = ["Prompt Role", "Input Data", "Output Contract", "Responsibility"]
    prompt_rows = [
        ["Vision Cataloger", "JPEG bytes + caption", "ExtractedGarment JSON", "Extracts category, colors, fit, fabric weight, formality tier, brand."],
        ["Identity Decider", "2 Garment dicts", "Boolean Match JSON", "Conservative match determining if two records represent the exact same piece."],
        ["Correction Refiner", "Previous JSON + Text", "Corrected JSON", "Applies plain-language user modifications authoritatively."],
        ["Stylist Engine", "Wardrobe + Weather + RAG", "OutfitRecommendation JSON", "Assembles harmonious multi-piece outfits with styling commentary."],
    ]
    p4.add_table(prompt_headers, prompt_rows, [85.0, 110.0, 105.0, 210.0], font_size=6.6)

    p4.add_heading1("8. Verification & Automated Test Suite")
    p4.add_paragraph("The test suite provides comprehensive coverage across all operational pathways:")
    p4.add_bullet("test_admin_pool.py", "Verifies admin test pool isolation, markdown escaping, and wardrobe action keyboards.")
    p4.add_bullet("test_batch_and_wardrobe.py", "Verifies multi-photo batch intake debouncing, 4-word title display caps, and category filtering.")
    p4.add_bullet("test_duplicate_detection.py", "Verifies 64-bit pHash calculations, hue similarity filters, and dual-condition linking.")
    p4.add_bullet("test_intake_flow.py", "Verifies HITL verification flow, single-item edits, and wardrobe navigation.")
    all_pages.append(p4)

    # PAGE 5: Environment Setup, Judge Guide & GitHub Workflow
    p5 = PageBuilder(5)
    p5.add_heading1("9. Environment Setup & Configuration")
    p5.add_paragraph("Step-by-step instructions to configure and execute the application:")
    setup_code = [
        "# 1. Create and activate virtual environment",
        "python3 -m venv .venv && source .venv/bin/activate",
        "",
        "# 2. Install production dependencies",
        "pip install -r requirements.txt",
        "",
        "# 3. Configure environment secrets (.env)",
        "cp .env.example .env",
        "# Fill in: TELEGRAM_BOT_TOKEN, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY",
        "",
        "# 4. Launch bot application",
        "python bot.py",
    ]
    p5.add_code_box(setup_code, font="F3", size=6.5, leading=7.8)

    p5.add_heading1("10. Zero-Friction Judge Evaluation Guide")
    p5.add_paragraph("Judges can test the stylist immediately without needing to photograph 10+ clothing items:")
    p5.add_bullet("Step 1: Activate Test Pool", "Send /admintest to the bot on Telegram and enter the demo password (demo123).")
    p5.add_bullet("Step 2: Inspect Wardrobe", "Send /wardrobe to browse pre-seeded items across Tops, Bottoms, Footwear, and Outerwear.")
    p5.add_bullet("Step 3: Test Styling Engine", "Send /style dinner date in Tokyo or /style casual rainy day to see live Weather RAG, DDGS web trend RAG, and Bedrock reasoning in action.")
    p5.add_bullet("Step 4: Exit Pool Mode", "Send /adminlive to return to your private user wardrobe session.")

    p5.add_heading1("11. VS Code to GitHub Upload Guide")
    p5.add_paragraph("Safe procedure to publish the project to GitHub without exposing secrets:")
    p5.add_bullet("1. Verify .gitignore", "Ensure .gitignore excludes .env, data/*.db, and data/images/* (run 'git status' to confirm).")
    p5.add_bullet("2. Stage & Commit in VS Code", "Open Source Control (Ctrl+Shift+G / Cmd+Shift+G) -> click '+' (Stage All) -> type commit message -> click Commit.")
    p5.add_bullet("3. Publish Repository", "Click 'Publish Branch' or 'Publish to GitHub' in VS Code, choose public/private, and push.")
    all_pages.append(p5)

    # Finalize footers on all pages
    total_pages = len(all_pages)
    for p in all_pages:
        p.finalize_footer(total_pages)

    # -------------------------------------------------------------
    # ASSEMBLE PDF DATA
    # -------------------------------------------------------------
    pdf_objs = []
    pdf_objs.append("<< /Type /Catalog /Pages 3 0 R /Outlines 2 0 R >>")
    pdf_objs.append("<< /Type /Outlines /Count 0 >>")
    pages_obj_idx = 3
    pdf_objs.append("") 
    pdf_objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    pdf_objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    pdf_objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")

    page_obj_ids = []
    
    for page in all_pages:
        stream_content = page.get_stream()
        stream_len = len(stream_content.encode("latin1"))
        
        content_idx = len(pdf_objs) + 1
        content_obj = f"<< /Length {stream_len} >>\nstream\n{stream_content}\nendstream"
        pdf_objs.append(content_obj)
        
        page_idx = len(pdf_objs) + 1
        page_obj = (
            f"<< /Type /Page /Parent {pages_obj_idx} 0 R "
            f"/MediaBox [0 0 595.28 841.89] "
            f"/Contents {content_idx} 0 R "
            f"/Resources << /Font << /F1 4 0 R /F2 5 0 R /F3 6 0 R >> /ProcSet [/PDF /Text /ImageB /ImageC /ImageI] >> >>"
        )
        pdf_objs.append(page_obj)
        page_obj_ids.append(page_idx)

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    pdf_objs[pages_obj_idx - 1] = f"<< /Type /Pages /Count {len(page_obj_ids)} /Kids [{kids_str}] >>"

    with open(output_path, "wb") as f:
        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, obj in enumerate(pdf_objs, 1):
            offsets.append(f.tell())
            f.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin1"))

        xref_offset = f.tell()
        f.write(f"xref\n0 {len(pdf_objs) + 1}\n".encode("latin1"))
        f.write(b"0000000000 65535 f \n")
        for off in offsets:
            f.write(f"{off:010d} 00000 n \n".encode("latin1"))

        f.write(f"trailer\n<< /Size {len(pdf_objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin1"))

    print(f"PDF successfully generated at: {output_path} ({total_pages} pages)")

if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else "system_documentation.pdf"
    create_pdf(out_file)
