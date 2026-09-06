#!/usr/bin/env python3
"""Generate a professional, executive-ready README.pdf for judge evaluation."""
import os
import sys

def create_readme_pdf(output_path: str):
    objects = []

    def add_object(content: str) -> int:
        objects.append(content)
        return len(objects)

    class PageBuilder:
        def __init__(self, page_num: int):
            self.page_num = page_num
            self.stream_lines = []
            self.width = 595.28   # A4 width in pt
            self.height = 841.89  # A4 height in pt
            self.margin_x = 42.0
            self.margin_top = 40.0
            self.margin_bottom = 40.0
            self.y = self.height - self.margin_top

            # Running header line
            self.stream_lines.append("0.75 0.75 0.78 RG 0.5 w")
            self.stream_lines.append(f"{self.margin_x} {self.height - 30} m {self.width - self.margin_x} {self.height - 30} l S")
            # Running header text
            self.stream_lines.append("BT /F1 7.5 Tf 0.45 0.45 0.50 rg")
            self.stream_lines.append(f"{self.margin_x} {self.height - 25} Td (AI Stylist & Smart Wardrobe Manager -- Project Documentation & Evaluation Guide) Tj ET")

        def finalize_footer(self, total_pages: int):
            # Running footer line
            self.stream_lines.append(f"0.75 0.75 0.78 RG 0.5 w")
            self.stream_lines.append(f"{self.margin_x} 36 m {self.width - self.margin_x} 36 l S")
            # Running footer text
            self.stream_lines.append(f"BT /F1 7.5 Tf 0.45 0.45 0.50 rg")
            self.stream_lines.append(f"{self.width - self.margin_x - 55} 25 Td (Page {self.page_num} of {total_pages}) Tj ET")
            self.stream_lines.append(f"BT /F1 7.5 Tf 0.45 0.45 0.50 rg")
            self.stream_lines.append(f"{self.margin_x} 25 Td (Official Submission Deliverable -- Agentic AI on Telegram) Tj ET")

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

            dot_y = self.y + 2.0
            self.stream_lines.append("0.18 0.42 0.82 rg")
            self.stream_lines.append(f"{self.margin_x + 2} {dot_y} 2.5 2.5 re f")

            self.stream_lines.append("BT /F1 7.6 Tf 0.18 0.18 0.22 rg")
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
            s = s.replace("•", "").replace("👗", "").replace("🧑‍⚖️", "").replace("🚀", "")
            s = s.replace("🔑", "").replace("📦", "").replace("⚡", "").replace("🎮", "")
            s = s.replace("📋", "").replace("🌟", "").replace("🏗️", "").replace("📂", "")
            s = s.replace("🧪", "").replace("💡", "").replace("📸", "")
            return s.encode("latin1", "replace").decode("latin1")

        def get_stream(self) -> str:
            return "\n".join(self.stream_lines)

    all_pages = []

    # PAGE 1: Overview & Judge Setup Guide
    p1 = PageBuilder(1)
    p1.add_title(
        "AI Stylist & Smart Wardrobe Manager -- README Documentation",
        "Agentic AI personal styling assistant delivered over Telegram with LangGraph, Multimodal Vision, and Live RAG"
    )
    p1.add_heading1("1. Project Executive Summary")
    p1.add_paragraph(
        "AI Stylist is an end-to-end agentic personal styling assistant delivered over Telegram. It transforms casual smartphone "
        "clothing photos into a structured digital wardrobe, incorporating Human-in-the-Loop (HITL) refinement, conservative "
        "dual-condition duplicate prevention, interactive body profiling, and a stateful LangGraph styling engine powered by "
        "live Open-Meteo weather, DuckDuckGo real-time fashion trends, past outfit history RAG, and deterministic color/proportion rules."
    )

    p1.add_heading1("2. Judge Quickstart & Environment Setup")
    p1.add_paragraph(
        "To evaluate the solution immediately without photographing and uploading 10+ garments, follow these quick steps "
        "to run the application with the pre-seeded evaluation wardrobe database:"
    )

    setup_code = [
        "# 1. Clone repository or extract submission code archive",
        "git clone https://github.com/Jerrywoohoo/Fashion-sense-bot.git",
        "cd Fashion-sense-bot",
        "",
        "# 2. Create and activate a clean Python virtual environment (Python 3.10+ / 3.12)",
        "python3 -m venv .venv && source .venv/bin/activate",
        "",
        "# 3. Install production dependencies",
        "pip install -r requirements.txt",
        "",
        "# 4. Configure .env with your credentials (or use provided Telegram Bot Token)",
        "cp .env.example .env",
        "# Fill in: TELEGRAM_BOT_TOKEN (provided in telegram_bot_info.txt), AWS credentials",
        "",
        "# 5. Launch the Bot Application",
        "python bot.py",
    ]
    p1.add_code_box(setup_code, font="F3", size=6.5, leading=7.9)

    p1.add_heading2("Evaluation Testing Database (wardrobe.db):")
    p1.add_paragraph(
        "The project includes a pre-seeded evaluation SQLite database (data/wardrobe.db) with verified items across Tops, "
        "Bottoms, Outerwear, Footwear, Accessories, and logged OOTD outfits so you can test recommendations immediately."
    )
    all_pages.append(p1)

    # PAGE 2: Telegram Testing & Command Reference
    p2 = PageBuilder(2)
    p2.add_heading1("3. Zero-Friction Judge Testing on Telegram")
    p2.add_paragraph("Follow these simple steps on Telegram to test the full agentic experience in under 2 minutes:")
    p2.add_bullet("Step 1: Open Bot", "Start the bot on Telegram by searching @Fashion_sense_bot (or your configured bot) and send /start.")
    p2.add_bullet("Step 2: Enter Judge Pool", "Send /admintest and enter evaluation password 'demo123' (or password set in your .env) to access the shared evaluation wardrobe.")
    p2.add_bullet("Step 3: Browse Wardrobe", "Send /wardrobe to inspect cataloged items across Tops, Bottoms, Outerwear, Footwear, and Accessories with badged photo previews and 2-step management.")
    p2.add_bullet("Step 4: Request Styling", "Send /style dinner date in Tokyo or /style rainy office day in London. The bot retrieves live weather forecasts, DuckDuckGo trend snippets, and compiles an optimal outfit.")
    p2.add_bullet("Step 5: Interactive Feedback", "Tap 'Item in Laundry' to test rotation cooldowns, or 'More Options' to re-roll combinations based on deterministic balance rules.")
    p2.add_bullet("Step 6: Exit Judge Mode", "Send /adminlive when finished to return to your isolated private wardrobe session.")

    p2.add_heading1("4. Available Telegram Commands Reference")
    cmd_headers = ["Command", "Scope", "Description & Usage"]
    cmd_rows = [
        ["/admintest", "Evaluation", "Switches to shared test wardrobe pre-seeded with items (Password: demo123)."],
        ["/adminlive", "Session", "Exits judge pool mode and returns to your isolated private wardrobe."],
        ["/wardrobe", "Inventory", "Browse cataloged pieces, badged photo previews, OOTD gallery, and laundry status."],
        ["/style [context]", "Stylist", "Triggers LangGraph styling engine with live weather RAG and web fashion trends."],
        ["/profile", "Onboarding", "7-step interactive wizard capturing body build, proportions, silhouettes, thermal preference."],
        ["/laundry", "Care Mgt", "View dirty laundry hamper and toggle items clean after doing laundry."],
        ["/delete [item_id]", "Wardrobe", "Delete individual items or execute a safe 2-step complete wardrobe reset."],
        ["/cancel", "Control", "Aborts any active prompt, intake correction, or conversation flow."],
    ]
    p2.add_table(cmd_headers, cmd_rows, [95.0, 75.0, 340.0], font_size=6.7)

    p2.add_heading1("5. End-to-End Agentic Lifecycle")
    lifecycle_ascii = [
        "+-------------------+   +-------------------+   +-------------------+   +-------------------+",
        "| 1. PHOTO INTAKE   |-->| 2. HITL REVIEW    |-->| 3. WARDROBE MGT   |-->| 4. AGENTIC STYLIST|",
        "| - Single & OOTD   |   | - Natural prompt  |   | - Clean 2-step    |   | - Live Weather RAG|",
        "| - Multi-piece     |   | - Granular dup    |   | - OOTD Gallery    |   | - DDGS Web Trends |",
        "| - Bedrock Vision  |   | - Non-blocking    |   | - Manual Linking  |   | - LangGraph Engine|",
        "+-------------------+   +-------------------+   +-------------------+   +-------------------+",
    ]
    p2.add_code_box(lifecycle_ascii, font="F3", size=6.5, leading=7.8)
    all_pages.append(p2)

    # PAGE 3: Methodology & Core Capabilities
    p3 = PageBuilder(3)
    p3.add_heading1("6. Detailed Methodology & System Capabilities")

    p3.add_heading2("1. Multimodal Vision Intake (app/extractor.py):")
    p3.add_bullet("Single & OOTD Classification", "Classifies uploaded photos into single-item photos or full multi-piece Outfit of the Day (OOTD) images.")
    p3.add_bullet("Rich Pydantic Extraction", "Extracts structured attributes: category, sub-category, primary/accent colors, silhouette fit, fabric weight, formality tier (1-5), and styling tags.")
    p3.add_bullet("Visual Image Badging", "Labels photos in memory with high-contrast, labeled visual badges using Pillow for crystal-clear user verification.")

    p3.add_heading2("2. Human-in-the-Loop (HITL) & Conservative Duplicate Linking (app/handlers.py):")
    p3.add_bullet("Unverified Staging", "Staged in unverified state (is_verified = 0) until user confirms or supplies natural-language revisions (e.g. 'the jacket is oversized charcoal wool').")
    p3.add_bullet("Non-Blocking Duplicate Selection", "Users can toggle duplicate candidates on/off without closing the capture card, keeping item edit buttons accessible before saving.")
    p3.add_bullet("Dual-Condition Deduplication", "Candidates must match both category and primary color hue before 64-bit pHash perceptual hashing and Bedrock LLM identity checks link them.")
    p3.add_bullet("Prominent Manual Duplicate Link", "Tap 'Manual Duplicate Link' anytime on intake cards or directly inside /wardrobe to link pieces across photos.")

    p3.add_heading2("3. 7-Step Profile Onboarding (app/profile_flow.py):")
    p3.add_bullet("Geometry & Thermal Preferences", "Captures body build, vertical proportions (e.g. long torso, broad shoulders), preferred silhouettes, and thermal preference (warm/cold).")

    p3.add_heading2("4. Contextual Agentic Stylist Graph (app/stylist_graph.py):")
    p3.add_bullet("Multi-Source Context RAG", "Combines live Open-Meteo weather forecasts, DuckDuckGo fashion trend snippets (ddgs), past outfit history, and 48-hour anti-repeat rotation cooldown.")
    p3.add_bullet("Deterministic Rule Matrix", "Enforces color harmony (monochromatic, complementary, analogous) and silhouette balance from app/style_matrix.py before LLM reasoning.")

    p3.add_heading2("5. Wardrobe & OOTD Gallery Management (/wardrobe, /laundry):")
    p3.add_bullet("OOTD Gallery", "Dedicated section displaying saved outfits, occasion labels, badged previews, and clear distinction between linked items and individual pieces.")
    p3.add_bullet("Unlinked Item Lifecycle", "Deleting an OOTD cleanly removes only unlinked garments while preserving previously existing wardrobe pieces.")
    all_pages.append(p3)

    # PAGE 4: Technical Architecture & Topology
    p4 = PageBuilder(4)
    p4.add_heading1("7. Technical Architecture & Topology")
    p4.add_paragraph(
        "The solution decouples stateful bot routing and storage (Google Cloud Platform Compute Engine) "
        "from generative AI compute (AWS Bedrock):"
    )

    topo_ascii = [
        "   +-------------------------------------------------------------------------+",
        "   |                      Telegram Client UI (User)                          |",
        "   |               (Photos, Captions, Interactive Inline Keyboards)          |",
        "   +-----------------------------------+-------------------------------------+",
        "                                       | MTProto Polling (HTTPX Keepalive)",
        "                                       v",
        "   +-----------------------------------+-------------------------------------+",
        "   |               Bot Application Router (bot.py)                           |",
        "   |        (Dual HTTPX Connection Pools, Debounce, Handlers)                |",
        "   +--------------------+--------------------------------+-------------------+",
        "                        |                                |",
        "         [Intake Flow]  |                                | [Stylist Flow]",
        "                        v                                v",
        "   +------------------------------+             +------------------------------+",
        "   | AWS Bedrock Vision           |             | LangGraph Stylist Engine     |",
        "   | (Amazon Nova Pro / Claude)   |             | (Context RAG + Rule FSM)     |",
        "   +--------------+---------------+             +--------------+---------------+",
        "                  |                                            |",
        "                  v                                            v",
        "   +------------------------------+             +------------------------------+",
        "   | Dual-Condition Deduplication |             | Live Weather & Trend RAG     |",
        "   | (pHash + Hue + LLM Link)     |             | (Open-Meteo + DuckDuckGo RAG)|",
        "   +--------------+---------------+             +--------------+---------------+",
        "                  |                                            |",
        "                  +----------------------+---------------------+",
        "                                         |",
        "                                         v",
        "   +-------------------------------------+-----------------------------------+",
        "   |            SQLite Database & Local Storage (data/wardrobe.db)           |",
        "   +-------------------------------------------------------------------------+",
    ]
    p4.add_code_box(topo_ascii, font="F3", size=6.3, leading=7.5)

    p4.add_heading1("8. LangGraph Stylist Node State Machine")
    graph_ascii = [
        "    [User /style] ---> 1. candidate_fetch_node (Open-Meteo, DDGS Trends, History, Clean Stock)",
        "                                 │",
        "                                 v",
        "                       2. trend_rules_node    (Color Harmony Wheel, Silhouette Balance, Thermal)",
        "                                 │",
        "                                 v",
        "                       3. stylist_llm_node    (AWS Bedrock Converse Reasoning & Assembly)",
        "                                 │",
        "                                 v",
        "                       4. outfit_critique_node (Inventory Validation, Formality & Cooldown)",
        "                                 │",
        "              +------------------+------------------+",
        "              │ (Pass)                              │ (Constraint Violation)",
        "              v                                     v",
        "    [Send Badged Photos & Action Keyboards]   [Deterministic Rule Fallback Re-seed]",
    ]
    p4.add_code_box(graph_ascii, font="F3", size=6.3, leading=7.6)
    all_pages.append(p4)

    # PAGE 5: Codebase Map, Tech Stack & Verification
    p5 = PageBuilder(5)
    p5.add_heading1("9. Project Directory & File Purpose Map")
    mod_headers = ["File / Module", "Layer", "Purpose & Architectural Role"]
    mod_rows = [
        ["bot.py", "Entrypoint", "Configures dual HTTPX connection pools, PTB Application, handlers, and resilient polling."],
        ["app/config.py", "Config", "Loads .env credentials, parses Settings dataclass, validates AWS and Telegram keys."],
        ["app/database.py", "Persistence", "SQLite connection manager, schema migrations, CRUD queries, and wear history logging."],
        ["app/extractor.py", "Vision / AI", "AWS Bedrock Converse API client, vision extraction prompts, 64-bit pHash, badge renderer."],
        ["app/handlers.py", "Controller", "Telegram commands (/wardrobe, /style, /laundry, /delete), intake debouncing, callbacks."],
        ["app/models.py", "Contracts", "Pydantic v2 schemas: ExtractedGarment, UserProfile, OutfitRecommendation."],
        ["app/paths.py", "Filesystem", "Resolves persistent data/ directories, SQLite DB paths, and image storage paths."],
        ["app/profile_flow.py", "Wizard", "7-step interactive /profile ConversationHandler with fallback command routing."],
        ["app/style_matrix.py", "Rule Engine", "Deterministic color harmony wheel, silhouette proportion rules, offline fallback stylist."],
        ["app/stylist_graph.py", "Agent Core", "Stateful LangGraph orchestrator executing RAG context -> LLM stylist -> Critique loop."],
        ["app/weather.py", "RAG Tool", "Open-Meteo REST client retrieving real-time temperature, precipitation, and UV index."],
        ["app/web_search.py", "RAG Tool", "DuckDuckGo (ddgs) client retrieving live occasion-specific fashion trend snippets."],
        ["data/wardrobe.db", "Data", "SQLite database containing pre-seeded demo wardrobe items for judge testing."],
    ]
    p5.add_table(mod_headers, mod_rows, [95.0, 70.0, 345.0], font_size=6.6)

    p5.add_heading1("10. Tech Stack & Dependencies")
    stack_headers = ["Layer", "Technology", "Version", "Purpose"]
    stack_rows = [
        ["Language", "Python", ">= 3.10", "Core asynchronous runtime"],
        ["Bot Framework", "python-telegram-bot", ">= 21.0", "Async MTProto Telegram API wrapper with polling"],
        ["LLM / Vision", "AWS Bedrock (Nova / Claude)", "Converse API", "Multimodal extraction & style reasoning"],
        ["Agent Orchestration", "LangGraph", ">= 0.2.0", "State machine with RAG & self-critique loop"],
        ["Data Contracts", "Pydantic", ">= 2.5.0", "Strict JSON schema validation and type safety"],
        ["Database", "SQLite 3", "Built-in", "Local relational storage with auto-migrations"],
        ["Image Processing", "Pillow (PIL)", ">= 10.0.0", "Resizing, 64-bit pHash, and labeled image badges"],
        ["Web Trends RAG", "duckduckgo-search / ddgs", ">= 6.0.0", "Live occasion & location fashion trend retrieval"],
        ["Weather API", "Open-Meteo API", "REST v1", "Keyless real-time weather & temperature forecasting"],
    ]
    p5.add_table(stack_headers, stack_rows, [85.0, 130.0, 75.0, 220.0], font_size=6.6)

    p5.add_heading1("11. Automated Test Suite (68 Tests Passing)")
    p5.add_paragraph("All modules are validated through an automated test suite executed via 'python -m unittest discover -s . -p \"test_*.py\"':")
    p5.add_bullet("test_system_resilience.py", "Verifies HTTPX polling connection timeouts, profile fallback command routing, and state resets.")
    p5.add_bullet("test_admin_pool.py", "Verifies admin test pool isolation, password gating, and wardrobe action keyboards.")
    p5.add_bullet("test_batch_and_wardrobe.py", "Verifies multi-photo batch intake debouncing, 4-word title display caps, and category filtering.")
    p5.add_bullet("test_duplicate_detection.py", "Verifies 64-bit pHash calculations, hue similarity filters, and dual-condition linking.")
    p5.add_bullet("test_intake_flow.py", "Verifies HITL verification flow, single-item edits, and wardrobe navigation.")
    all_pages.append(p5)

    # Finalize footers
    total_pages = len(all_pages)
    for p in all_pages:
        p.finalize_footer(total_pages)

    # Assemble PDF
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

    print(f"README PDF successfully generated at: {output_path} ({total_pages} pages)")

if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else "README.pdf"
    create_readme_pdf(out_file)
