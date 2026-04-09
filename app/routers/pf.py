"""
Plausible Fiction: refine user prompt and generate post content via OpenAI.
"""
from fastapi import APIRouter, HTTPException
import base64
from io import BytesIO
import httpx
from PIL import Image

from app.config import settings
from pydantic import BaseModel

router = APIRouter(prefix="/pf", tags=["plausible-fiction"])


class GenerateRequest(BaseModel):
    prompt: str


class GenerateResponse(BaseModel):
    refined_prompt: str
    content: str | None = None  # None when prompt was invalid → only show refined idea (apology)


class ModifyRequest(BaseModel):
    current_content: str
    user_message: str


class ModifyResponse(BaseModel):
    content: str


class GenerateImageRequest(BaseModel):
    post_content: str


class GenerateImageResponse(BaseModel):
    image_url: str


def _post_content_to_image_prompt(post_content: str) -> str:
    """Turn post text into a short, visual image prompt for DALL-E (no Key tasks section)."""
    text = (post_content or "").strip()
    if not text:
        return "A hopeful, inspiring scene about building a better future."
    if "key tasks" in text.lower():
        text = text.split("key tasks")[0].strip()
    first_part = text.split("\n\n")[0].replace("\n", " ").strip()
    if len(first_part) > 800:
        first_part = first_part[:797] + "..."
    return (
        f"Professional, aspirational image illustrating this idea: {first_part}. "
        "Style: modern, optimistic, clean composition. No text in the image."
    )


# Max dimension for feed display; keeps file size down while looking sharp in UI
_IMAGE_MAX_PX = 800
_WEBP_QUALITY = 85


def _generate_image(prompt: str) -> str:
    """Generate one image from prompt via DALL-E 3. Returns URL (valid ~60 min)."""
    from openai import OpenAI

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="Image generation requires OPENAI_API_KEY.",
        )
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        response_format="url",
        n=1,
    )
    if not response.data or len(response.data) == 0:
        raise HTTPException(status_code=500, detail="No image returned.")
    image_url = response.data[0].url

    try:
        with httpx.Client(timeout=30.0) as http:
            resp = http.get(image_url)
            resp.raise_for_status()
            raw_bytes = resp.content
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch generated image.") from e

    try:
        img = Image.open(BytesIO(raw_bytes)).convert("RGB")
        w, h = img.size
        if max(w, h) > _IMAGE_MAX_PX:
            ratio = _IMAGE_MAX_PX / max(w, h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, "WEBP", quality=_WEBP_QUALITY, method=6)
        out.seek(0)
        b64 = base64.b64encode(out.read()).decode("ascii")
        return f"data:image/webp;base64,{b64}"
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to optimize image.") from e


def _refine_prompt(raw: str) -> str:
    from openai import OpenAI

    if not settings.openai_api_key:
        return raw.strip()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful editor. Given a user's rough idea or prompt for a short social post "
                    "(a 'plausible future' or idea they want to share), correct any grammar or spelling, "
                    "and refine it into one clear, concise sentence that captures their intent. "
                    "Do not add new ideas—only clarify and polish. Reply with only the refined sentence, nothing else. "
                    "If the user's input is not a coherent idea at all (gibberish, random characters, empty meaning, "
                    "or clearly not a prompt for a post), reply with only this exact message and nothing else: "
                    "I'm sorry, but that doesn't seem like a clear idea or prompt. Could you please share a specific "
                    "concept or idea you'd like to turn into a post?"
                ),
            },
            {"role": "user", "content": raw.strip() or "Share an idea."},
        ],
        max_tokens=150,
    )
    text = (response.choices[0].message.content or "").strip()
    return text or raw.strip()


def _is_refinement_apology(refined: str) -> bool:
    """True if the refined 'prompt' is an apology (invalid input), so we should not generate a post."""
    lower = refined.lower().strip()
    if not lower:
        return False
    indicators = (
        "i'm sorry",
        "i am sorry",
        "could you please",
        "please share",
        "not a coherent",
        "doesn't seem like a clear",
        "does not seem",
    )
    return any(ind in lower for ind in indicators)


def _generate_post(refined_prompt: str) -> str:
    from openai import OpenAI

    if not settings.openai_api_key:
        return (
            f"[Demo] Post based on: {refined_prompt}. "
            "Set OPENAI_API_KEY in the backend to generate real content."
        )
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You write short, engaging social posts for 'Weve'—a platform for sharing "
                    "'plausible futures' (ideas, visions, or plans people want to work toward). "
                    "Write in first person, conversational tone. One or two short paragraphs for the main post. "
                    "No hashtags. Do not add placeholders for images; the user can attach a photo in the app.\n\n"
                    "After the post paragraphs, add a line that says exactly 'Key tasks:' (with a colon), "
                    "then on the next lines list 3 to 5 key action items as a numbered list (1. ... 2. ...). "
                    "Each item should be one short line (under 100 chars). "
                    "Output only the post text plus the Key tasks section, nothing else."
                ),
            },
            {"role": "user", "content": f"Write a post based on this idea: {refined_prompt}"},
        ],
        max_tokens=420,
    )
    text = (response.choices[0].message.content or "").strip()
    return text or "[No content generated.]"


def _modify_post(current_content: str, user_message: str) -> str:
    from openai import OpenAI

    if not settings.openai_api_key:
        return (
            f"[Demo] Modified: {current_content[:80]}... "
            "Set OPENAI_API_KEY for real edits."
        )
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are helping the user edit a short social post for 'Weve' (a platform for plausible futures). "
                    "The user will give you the current post text and an instruction (e.g. 'make it shorter', "
                    "'add a call to action', 'tone it down', 'expand the second paragraph'). "
                    "Return ONLY the revised post text—no explanations, no quotes. "
                    "Keep the same tone and intent; only apply the requested change. "
                    "If the current text has a 'Key tasks:' section with numbered items, keep it or update it as needed. "
                    "If there is no Key tasks section, you may add one (a line 'Key tasks:' followed by 3-5 numbered items). "
                    "One or two short paragraphs for the main post, then Key tasks. No hashtags. "
                    "Do not add placeholders for images; the app lets the user attach a photo separately."
                ),
            },
            {
                "role": "user",
                "content": f"Current post:\n\n{current_content}\n\nUser request: {user_message.strip()}",
            },
        ],
        max_tokens=350,
    )
    text = (response.choices[0].message.content or "").strip()
    return text or current_content


@router.post("/generate", response_model=GenerateResponse)
async def generate_post(body: GenerateRequest):
    """Refine the user's prompt, then generate a post from it. No auth required for now."""
    raw = (body.prompt or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Prompt is required.")
    try:
        refined = _refine_prompt(raw)
        if _is_refinement_apology(refined):
            return GenerateResponse(refined_prompt=refined, content=None)
        content = _generate_post(refined)
        return GenerateResponse(refined_prompt=refined, content=content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Generation failed: {getattr(e, 'message', str(e))}",
        ) from e


@router.post("/modify", response_model=ModifyResponse)
async def modify_post(body: ModifyRequest):
    """Modify an existing post based on the user's instruction (e.g. shorten, expand, rewrite)."""
    content = (body.current_content or "").strip()
    msg = (body.user_message or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Current content is required.")
    if not msg:
        raise HTTPException(status_code=400, detail="User message is required.")
    try:
        new_content = _modify_post(content, msg)
        return ModifyResponse(content=new_content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Modify failed: {getattr(e, 'message', str(e))}",
        ) from e


@router.post("/generate-image", response_model=GenerateImageResponse)
async def generate_image(body: GenerateImageRequest):
    """Generate an image from the post content (DALL-E 3). Returns a temporary URL (~60 min)."""
    content = (body.post_content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Post content is required.")
    try:
        prompt = _post_content_to_image_prompt(content)
        url = _generate_image(prompt)
        return GenerateImageResponse(image_url=url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Image generation failed: {getattr(e, 'message', str(e))}",
        ) from e
