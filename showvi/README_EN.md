<p align="center">
  <img src="assets/readme/hero-banner.png" alt="Showvi AI — From Idea to Film in One Click" width="100%">
</p>

<p align="center">
  <strong>AI Video Director — From a single idea to a finished film, fully automated by multi-agent collaboration</strong>
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <b>English</b>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Seedance 2.0" src="https://img.shields.io/badge/Seedance_2.0-Jimeng-06b6d4?style=flat-square">
  <img alt="Gemini" src="https://img.shields.io/badge/Gemini-LLM_&amp;_Image_Gen-4285F4?style=flat-square&logo=google&logoColor=white">
  <img alt="GPT Image" src="https://img.shields.io/badge/gpt--image--2-Image_Gen-412991?style=flat-square&logo=openai&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=flat-square">
</p>

---

## What is Showvi?

**Showvi** is an end-to-end automated video production system driven by multiple collaborating AI agents. Just provide a one-line idea, a novel excerpt, or a reference video — a team of specialized agents will take over the entire pipeline: screenplay, storyboard, reference image generation, video generation, quality review, intelligent prompt rewriting, and final editing.

At its core, Showvi is not "one API call = one video." It is a **self-optimizing production loop**: every generated clip is reviewed by **Gemini VLM** across multiple dimensions; clips that fall short are automatically rewritten and regenerated until they pass. Reference image generation supports **Google Gemini** and **gpt-image-2** backends. Video generation runs on **Seedance 2.0**, automatically submitting tasks through the Jimeng web interface under your own account in **VIP or non-VIP queue mode**, saving time and credits with zero manual supervision.

---

## Key Features

### 📥 Multiple Creative Inputs

| | Input | Description |
| :---: | :--- | :--- |
| 💡 | **One-liner Idea** | Type a single creative prompt — AI expands it into a full screenplay and produces the video |
| 📖 | **Novel-to-Video** | Paste a novel chapter or story — AI breaks down the narrative, storyboards it, and generates video |
| 🔁 | **Video Replication** | Upload a reference video — AI analyzes shot language and pacing to produce new content in the same style |
| ✂️ | **Video Remix** | Remix existing footage with a new narrative and visual arrangement |

### ⚙️ Technical Highlights

| Feature | Description |
| :--- | :--- |
| 🤖 **Multi-Agent Storyboarding** | 6 specialized LLM agents in relay: Screenplay → Dependency Grouping → Entity State Tracking → Storyboard Conversion → Continuity Director → Auto Validation & Fix |
| 🧬 **Entity Continuity Tracking** | Detects cross-segment state changes — costume swaps, injuries, transformations, prop damage — and generates derived reference images |
| 🎬 **Seedance 2.0 Automation** | Browser-automated Jimeng submission; non-VIP queue mode saves credits; supports seedance-2.0 / fast / vip models |
| 🔬 **VLM Quality Loop** | Generate → Critique → Rewrite → Retry; Gemini reviews visual quality, character consistency, physical plausibility, and more |
| 🧩 **Reference Image Consistency** | Character / scene / prop reference images auto-generated and VLM-verified before injection into video generation |
| 🔗 **Cross-Shot Continuity** | 16-grid keyframe passing + transition bridging + continuity director ensures seamless visual flow across segments |
| 🛡️ **Adaptive Safety Rewrite** | Auto-detects 6 violation categories (IP, violence, NSFW, etc.) and progressively rewrites prompts to pass moderation |
| ⚡ **Checkpoint & Resume** | Every step is checkpointed; crashes auto-recover in-flight cloud tasks; already-replaced assets are protected |

---

## 🤖 Multi-Agent Storyboard Pipeline

Storyboarding is not a single LLM call — it is a **6-agent relay pipeline**:

> 📥 Creative Input (one-liner / novel / reference video)

| Step | Agent | What it does |
| :---: | :--- | :--- |
| **1** | 🎭 Screenplay Generator | Generates a full narrative with dramatic structure (hook → conflict → climax → payoff) |
| **2** | 🔗 Dependency Grouper | Analyzes spatial continuity between segments; determines serial vs. parallel generation order |
| **3** | 🧬 Entity State Tracker | Two-pass LLM analysis; detects costume changes / injuries / transformations and registers derived entities |
| **4** | 🎬 Storyboard Converter | Narrative segments → technical storyboard with camera movement, lighting, and visual descriptions |
| **5** | 🎼 Continuity Director | Global polish of all segment Seedance prompts for smooth visual and narrative transitions |
| **6** | ✅ Validator & Fixer | Auto-corrects character aliases, dialogue attribution, and continuity text errors; outputs executable storyboard JSON |

> 📤 Output: Structured storyboard JSON (with character / scene / prop definitions and derived entity variants)

---

## 🔬 VLM Quality Loop

Every generated clip enters an automatic review → rewrite → retry loop until it meets the quality threshold or exhausts the retry budget:

> **Generate** → **VLM Critique** (score ≥ 7 passes) → fail → **Rewrite Prompt** → re-**Generate** → … → pass → **Candidate Pool** → VLM **Best-of-N Selection** → **Final Assembly**

Gemini VLM reviews each clip across **6 dimensions**: visual quality, content accuracy, character consistency, artistry, physical plausibility, and model clipping. Critique feedback is automatically fed to the prompt-rewriting agent for targeted fixes. Among multiple attempts, VLM selects the best clip per segment. Even if none meet the threshold, the highest-scoring clip is used as a fallback — no segment is left empty.

---

## 🎬 Production Pipeline

From creative input to final output — 7 fully automated stages:

| Step | Stage | Description |
| :---: | :--- | :--- |
| **1** | 📥 Creative Input | One-liner idea / novel chapter / reference video — three entry points |
| **2** | 🤖 Multi-Agent Storyboard | 6-step agent relay: screenplay → grouping → state tracking → storyboard → continuity director → validation |
| **3** | 🖼️ Reference Image Gen | Character / scene / prop reference images generated in parallel, VLM-verified for consistency |
| **4** | 🎬 Video Generation | Seedance 2.0 auto-queued via Jimeng account; VIP / non-VIP modes; parallel workers |
| **5** | 🔬 VLM Quality Review | Gemini multi-dimensional critique → rewrite prompt on failure → regenerate |
| **6** | 🏆 Smart Selection | VLM compares candidate clips and picks the best per segment |
| **7** | 🎞️ Final Output | Audio loudness normalization + crossfade transitions → `final_video.mp4` |

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
git clone https://github.com/sjtuplayer/showvi.git
cd showvi

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your API keys (see [Configuration](#-configuration) below).

### 3. Launch the Web Dashboard

```bash
python -m dashboard.server --port 8501
```

### 4. Open Your Browser

Visit **http://localhost:8501** to enter the Showvi Dashboard and start creating.

The Dashboard provides a full visual interface: one-click video creation, screenplay generation, production monitoring, screenplay library, asset library, and system settings — everything in the browser.

---

## ⚙️ Configuration

Edit the `.env` file to configure three required services. See [`.env.example`](.env.example) for all options.

You can also configure everything from the Dashboard settings page.

### 1. LLM (Large Language Model)

Two providers are supported — switch in the Dashboard settings or `.env`:

| Provider | Config | Notes |
| :--- | :--- | :--- |
| **Google** | `LLM_PROVIDER=google` | Native video understanding; ideal for video critique, storyboarding, and multimodal tasks |
| **OpenAI-compatible** | `LLM_PROVIDER=openai_compatible` | Works with DeepSeek / Moonshot / Qwen / OpenRouter, etc. |

```bash
# Google
LLM_PROVIDER=google
GEMINI_API_KEY=your-gemini-api-key
LLM_MODEL=gemini-2.5-flash

# Or OpenAI-compatible
# LLM_PROVIDER=openai_compatible
# LLM_BASE_URL=https://api.deepseek.com/v1
# LLM_API_KEY=your-key
# LLM_MODEL=deepseek-chat
```

Per-step model overrides (optional):

```bash
# LLM_MODEL_SCREENPLAY_GEN=deepseek-reasoner   # Use a reasoning model for screenplays
# LLM_PROVIDER_VIDEO_CRITIQUE=google            # Video critique needs video understanding
# LLM_MODEL_VIDEO_CRITIQUE=gemini-2.5-pro
```

### 2. Image Generation

Two providers are supported:

| Provider | Config | Notes |
| :--- | :--- | :--- |
| **Google** | `IMAGE_PROVIDER=google` | Uses Gemini image generation; reuses `GEMINI_API_KEY`, no extra key needed |
| **OpenAI-compatible** | `IMAGE_PROVIDER=openai_compatible` | Supports gpt-image-2 / DALL-E 3 / Flux, etc. |

```bash
# Google (reuses GEMINI_API_KEY)
IMAGE_PROVIDER=google
IMAGE_MODEL=gemini-2.0-flash-preview-image-generation

# Or OpenAI-compatible
# IMAGE_PROVIDER=openai_compatible
# IMAGE_BASE_URL=https://api.openai.com/v1
# IMAGE_API_KEY=your-key
# IMAGE_MODEL=gpt-image-2
```

### 3. Video Generation — Jimeng Seedance 2.0

```bash
SEEDDANCE_SESSION_ID=your-session-id
SEEDDANCE_BACKEND=jimeng
```

<details>
<summary><b>How to get SEEDDANCE_SESSION_ID</b></summary>

1. Open [Jimeng](https://jimeng.jianying.com) in your browser and log in
2. Press `F12` to open Developer Tools, switch to the **Application** tab
3. Expand **Cookies** on the left → select `https://jimeng.jianying.com`
4. Find `sessionid` in the cookie list and copy its **Value**
5. Paste the value into `SEEDDANCE_SESSION_ID` in your `.env` file

</details>

The agent defaults to **non-VIP mode** (`seedance-2.0`), automatically queuing generations without consuming expensive VIP credits. Switch to `seedance-2.0-vip` if you need faster generation (costs more credits).

> **About jimeng CLI:** The current version submits tasks via the Jimeng web interface. Since the jimeng CLI is not yet fully featured, a future release will migrate to the jimeng CLI for Seedance calls, eliminating the need to manually obtain a session_id.

See [`.env.example`](.env.example) for the full list of configuration options.

---

## 📄 License

[MIT](LICENSE)
