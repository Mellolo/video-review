# Custom Provider Plugin Guide

Place your custom LLM or image generation client here as a `.py` file.
The system auto-discovers any module that declares `PLUGIN_TYPE` and `PLUGIN_CLASS`.

## Image Plugin Example

```python
# clients/custom/my_image_provider.py
import os
from typing import Optional, List

class MyImageClient:
    """Custom image generation client."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, **kwargs):
        self.api_key = api_key or os.getenv("MY_IMAGE_API_KEY", "")
        self.model = model or "my-default-model"

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        image_size: str = "2K",
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 1,
        reference_images: Optional[List[str]] = None,
        **kwargs,
    ) -> bytes:
        """Return raw image bytes."""
        # Your implementation here
        raise NotImplementedError

# Required: declare plugin type and class
PLUGIN_TYPE = "image"
PLUGIN_CLASS = MyImageClient
```

Then set in your `.env`:
```
IMAGE_PROVIDER=custom:my_image_provider
MY_IMAGE_API_KEY=your-key-here
```

## LLM Plugin Example

```python
# clients/custom/my_llm_provider.py
import os
from typing import Optional, List, Dict, Any

class MyLLMClient:
    """Custom LLM client."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, **kwargs):
        self.api_key = api_key or os.getenv("MY_LLM_API_KEY", "")
        self.default_model = model or "my-default-model"

    def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        raise NotImplementedError

    def generate_with_vision(
        self,
        text_prompt: str,
        image_paths: List[str],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        raise NotImplementedError

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: int = 3,
    ) -> str:
        raise NotImplementedError

# Required: declare plugin type and class
PLUGIN_TYPE = "llm"
PLUGIN_CLASS = MyLLMClient
```

Then set in your `.env`:
```
LLM_PROVIDER=custom:my_llm_provider
MY_LLM_API_KEY=your-key-here
```

## Per-Step Override

You can use different providers for different pipeline steps:

```bash
# Use custom image provider globally
IMAGE_PROVIDER=custom:my_image_provider

# Use Google for video analysis (needs video understanding)
LLM_PROVIDER_VIDEO_ANALYSIS=google
LLM_PROVIDER_VIDEO_CRITIQUE=google

# Use a cheap model for prompt rewriting
LLM_MODEL_PROMPT_REWRITE=deepseek-chat

# Use a powerful model for screenplay generation
LLM_MODEL_SCREENPLAY_GEN=deepseek-reasoner
```

## Available Step IDs

| Step ID | Description |
|---------|-------------|
| `screenplay_gen` | Screenplay generation from prompt/novel |
| `storyboard_gen` | Storyboard segmentation |
| `video_analysis` | Video-to-screenplay analysis |
| `prompt_rewrite` | Image prompt rewriting (safety/optimization) |
| `scene_rewrite` | Scene rewriting after failures |
| `scene_edit` | Manual scene editing |
| `video_critique` | Video quality evaluation |
| `video_select` | Best video selection |
| `style_check` | Style consistency checking |
| `transition` | Transition bridge generation |
| `metadata_sync` | Screenplay metadata synchronization |
