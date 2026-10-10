"""
ConsultCastAI persona and scenario content model.

Six SMB owner personas across different industries, each skeptical of AI
adoption for a different reason (cost, trust, data risk, staff adoption,
job displacement, "I already tried ChatGPT"). This is the practice surface
an AI consultant rehearses against before a real discovery or sales call.

In production this moves to Firestore/Postgres behind an admin screen. Here
it's a plain dict so the rest of the app is written against the real shape
and can be repointed later without touching coaching.py, prompts.py, or
main.py.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Persona:
    id: str
    name: str
    role: str
    context: str
    traits: str
    # Anam live-avatar IDs. Empty until published in Anam Lab; the
    # /avatar/session-token route 409s for any persona without both set.
    avatar_id: str = ""
    voice_id: str = ""
    avatar_model: str = ""
    industry: str = ""
    # Short chips shown in the UI beside the `traits` paragraph. Display-only:
    # the paragraph is what feeds the system prompt, these are never sent to
    # Claude. Each item: {"label": str, "icon": str (an emoji), "color": str
    # (hex, used only for the chip's small icon-circle background)}.
    trait_tags: list[dict] = field(default_factory=list)
    # Static label shown in the persona card ("Easy" / "Medium" / "Hard").
    # Display-only, hand-set from how hard each persona's personality is to
    # work with; nothing computes it and it never reaches the prompts.
    difficulty: str = "Medium"
    # Pre-session prep tips shown in the left panel. Display-only, same
    # shape as trait_tags: {"text": str, "icon": str (an emoji), "color": str
    # (hex, used only for the tip's small icon-circle background)}.
    coaching_tips: list[dict] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    persona_id: str
    group: str  # objection category, see GROUPS below
    title: str
    product: str
    # No longer shown verbatim: prompts.build_opener_prompt hands this to
    # Claude as the substance of the objection to raise, rephrased live to
    # fit whichever call_direction the session was started with. Kept as
    # plain text (not per-direction variants) since it's direction-neutral,
    # a client-side objection can plausibly open either kind of call.
    opener: str
    chips: list[str] = field(default_factory=list)
    briefing: str = ""  # one or two sentences of scene-setting shown before the opener
    active: bool = True


GROUPS = {
    "cost_roi": "Cost & ROI",
    "already_tried": "\"We already use ChatGPT\"",
    "job_loss": "Staff / Job Displacement Fear",
    "data_privacy": "Data & Privacy Risk",
    "adoption": "Staff Won't Adopt It",
    "trust_accuracy": "Trust & Accuracy",
}

PERSONAS: dict[str, Persona] = {
    # NOTE (avatar art): the custom Anam avatar image generated for Carla
    # includes incidental, AI-generated text on the shirt reading "Cafe Sol,"
    # which does not match "Diaz Family Kitchen" below. AI image generators
    # frequently render garbled or invented text/logos on clothing; this is
    # known, was reviewed, and was accepted as-is (not corrected) rather than
    # regenerating the image. It has no effect on persona behavior, prompts,
    # or coaching logic, cosmetic only.
    "carla_diaz": Persona(
        id="carla_diaz",
        name="Carla Diaz",
        role="Owner, Diaz Family Kitchen (independent restaurant, 3 locations)",
        context=(
            "Runs a 3-location Tex-Mex restaurant group with her brother. Thin "
            "margins, high turnover kitchen and front-of-house staff, most "
            "scheduling and ordering still runs on whiteboards and group texts. "
            "Burned two years ago by a POS vendor that oversold what the system "
            "could do."
        ),
        traits=(
            "Warm but guarded. Talks fast, interrupts when she smells a pitch. "
            "Thinks in terms of the Friday dinner rush, not quarterly strategy. "
            "Respects people who've actually worked a floor."
        ),
        avatar_id="78cceaf3-fecc-49d6-b752-05f9e498a7bd",
        voice_id="c48c4dd9-5050-11f1-9076-5e955d484d11",
        avatar_model="cara-4",
        industry="restaurant",
        difficulty="Medium",
        trait_tags=[
            {"label": 'Warm but guarded', "icon": '🛡️', "color": '#5b7fff'},
            {"label": 'Talks fast', "icon": '💨', "color": '#9b5cff'},
            {"label": 'Interrupts', "icon": '⚡', "color": '#ffb84d'},
            {"label": 'Ops-focused', "icon": '⚙️', "color": '#4de8ff'},
            {"label": 'Respects hands-on experience', "icon": '🤝', "color": '#ff5d7a'},
        ],

        coaching_tips=[
            {"text": 'Lead with numbers, not sales language', "icon": '💵', "color": '#4de8ff'},
            {"text": "She respects people who've actually worked a floor", "icon": '👨\u200d🍳', "color": '#ffb84d'},
            {"text": 'Expect to be interrupted if you ramble', "icon": '⚡', "color": '#ff5d7a'},
            {"text": 'Talk in terms of a bad Friday night, not quarterly strategy', "icon": '🌙', "color": '#9b5cff'},
        ],
    ),
    "harold_bennett": Persona(
        id="harold_bennett",
        name="Harold Bennett",
        role="Managing Partner, Bennett & Cross (6-attorney boutique law firm)",
        context=(
            "Runs a small litigation and estate-planning practice. Personally "
            "reviews every client-facing document before it goes out. Has heard "
            "AI horror stories about lawyers citing fake cases and treats that as "
            "confirmed fact about all AI tools, not an edge case."
        ),
        traits=(
            "Precise, a little condescending, speaks like he's cross-examining. "
            "Cares about liability and client confidentiality above everything "
            "else. Will not be rushed and enjoys finding the flaw in an argument."
        ),
        # From Anam's stock library via the Persona builder (persona
        # 3742ecc4-3342-59e2-a832-f0320624ec78): stock avatar "Richard"
        # (table variant), stock voice "Gabriel". As with Tom, the avatar ID
        # is the persona's `avatar.id` from GET /v1/personas/{id}, and the
        # persona itself (with its own prompt and Anam LLM) is not used for
        # sessions.
        avatar_id="19d18eb0-5346-4d50-a77f-26b3723ed79d",
        voice_id="8246d9f7-827e-4a5c-8697-644ce860ca02",
        avatar_model="cara-4",
        industry="legal",
        difficulty="Hard",
        trait_tags=[
            {"label": 'Precise', "icon": '🎯', "color": '#5b7fff'},
            {"label": 'A little condescending', "icon": '🧐', "color": '#9b5cff'},
            {"label": 'Cross-examines', "icon": '🔍', "color": '#4de8ff'},
            {"label": 'Liability-focused', "icon": '⚖️', "color": '#ff5d7a'},
            {"label": 'Unhurried', "icon": '🕰️', "color": '#ffb84d'},
        ],

        coaching_tips=[
            {"text": 'Never rush him, he sets the pace', "icon": '🕰️', "color": '#ffb84d'},
            {"text": "Cite specifics, he'll catch vague claims immediately", "icon": '🔍', "color": '#4de8ff'},
            {"text": 'Confidentiality and liability come before any pitch', "icon": '🔒', "color": '#ff5d7a'},
            {"text": 'Expect to be cross-examined on every claim', "icon": '⚖️', "color": '#5b7fff'},
        ],
    ),
    "priya_nair": Persona(
        id="priya_nair",
        name="Priya Nair",
        role="Founder, Nair Home Goods (DTC e-commerce, ~$2M/yr)",
        context=(
            "Solo founder of a home goods e-commerce brand, small remote team of "
            "4. Already uses ChatGPT daily for product descriptions and customer "
            "email replies. Sees consultants as people who repackage free tools "
            "at a markup."
        ),
        traits=(
            "Sharp, direct, mildly impatient. Genuinely tech-curious, not "
            "anti-AI, just anti-being-sold-something-she-already-has. Responds "
            "well to being shown something specific she hasn't seen."
        ),
        # From Anam's stock library via the Persona builder (persona
        # 2abad9bf-d80b-5711-b25d-8ce421bb2871): stock avatar "Zara", stock
        # voice "Sophie - Legacy". As with Tom, the avatar ID is the
        # persona's `avatar.id` from GET /v1/personas/{id}, and the persona
        # itself (with its own prompt and Anam LLM) is not used for sessions.
        avatar_id="d407ae83-7b53-4988-b4f9-0b1c8243bac9",
        voice_id="c30f84e2-eb31-4611-8c75-64e606c51eb3",
        avatar_model="cara-4",
        industry="ecommerce",
        difficulty="Medium",
        trait_tags=[
            {"label": 'Sharp', "icon": '💡', "color": '#ffb84d'},
            {"label": 'Direct', "icon": '➡️', "color": '#5b7fff'},
            {"label": 'Tech-curious', "icon": '🔬', "color": '#9b5cff'},
            {"label": 'Impatient with fluff', "icon": '⏱️', "color": '#ff5d7a'},
            {"label": 'Data-driven', "icon": '📊', "color": '#4de8ff'},
        ],

        coaching_tips=[
            {"text": "She's already using AI, don't explain what she knows", "icon": '🚫', "color": '#ff5d7a'},
            {"text": "Show, don't tell, concrete examples win", "icon": '👀', "color": '#4de8ff'},
            {"text": 'Get to the point fast, she has zero patience for fluff', "icon": '⚡', "color": '#ffb84d'},
            {"text": 'Numbers and data move her more than adjectives', "icon": '📊', "color": '#5b7fff'},
        ],
    ),
    "dr_owens": Persona(
        id="dr_owens",
        name="Dr. Renee Owens",
        role="Office Manager, Owens Family Dental (2-dentist practice)",
        context=(
            "Runs the business side of a 2-dentist, 9-employee dental practice. "
            "Not a doctor herself, handles scheduling, billing, and HIPAA "
            "compliance. Deeply risk-averse about anything touching patient "
            "data. Staff already anxious about layoffs after a nearby practice "
            "automated its front desk."
        ),
        traits=(
            "Careful, procedural, asks for things in writing. Protective of her "
            "front-desk staff, several of whom have been there over a decade. "
            "Will shut a conversation down fast if she senses risk without a "
            "clear compliance answer."
        ),
        # From Anam's stock library via the Persona builder (persona
        # 0498eb5a-5877-53eb-9f8c-f73828505cec): stock avatar "Nour", stock
        # voice "Sophie". As with Tom, the avatar ID is the persona's
        # `avatar.id` from GET /v1/personas/{id}, and the persona itself
        # (with its own prompt and Anam LLM) is not used for sessions.
        avatar_id="7c502c6c-cd62-401b-bec7-765d32a0769a",
        voice_id="1c6fa8a7-9aa4-4a17-a75e-3e5eb863fccf",
        avatar_model="cara-4",
        industry="dental",
        difficulty="Hard",
        trait_tags=[
            {"label": 'Careful', "icon": '⚠️', "color": '#ffb84d'},
            {"label": 'Procedural', "icon": '📋', "color": '#9b5cff'},
            {"label": 'Protective of staff', "icon": '🛡️', "color": '#ff5d7a'},
            {"label": 'Risk-averse', "icon": '🚫', "color": '#5b7fff'},
            {"label": 'Wants it in writing', "icon": '✍️', "color": '#4de8ff'},
        ],

        coaching_tips=[
            {"text": 'Compliance and data privacy come before anything else', "icon": '🔐', "color": '#5b7fff'},
            {"text": "Reassure her about her staff's jobs, not just efficiency", "icon": '🛡️', "color": '#ff5d7a'},
            {"text": "She wants everything in writing, verbal promises won't land", "icon": '📝', "color": '#4de8ff'},
            {"text": "Move slowly, she doesn't like being rushed into decisions", "icon": '🐢', "color": '#ffb84d'},
        ],
    ),
    "tom_walsh": Persona(
        id="tom_walsh",
        name="Tom Walsh",
        role="Owner, Walsh Roofing & Exteriors (18 employees)",
        context=(
            "Second-generation roofing contractor. Runs the business off a "
            "flip phone half the day and a laptop the other half. Not "
            "anti-technology, just has no spare time and no patience for "
            "anything with a learning curve. Crews are older, several have "
            "worked for him 15+ years."
        ),
        traits=(
            "Plainspoken, friendly, easily distracted by an actual job problem. "
            "Measures everything in hours saved or hours wasted. Suspicious of "
            "anything that sounds like it needs 'training' to use."
        ),
        # Built in Anam's Persona builder (persona 02832c42-aed4-4861-95aa-
        # c2d58650bec2), which shows no avatar ID. It is that persona's
        # `avatar.id` from GET /v1/personas/{id}. The persona itself is not
        # used for sessions: a `personaId` token runs the persona's own Anam
        # LLM, and here Claude must be the only one answering (see
        # anam_client.py).
        avatar_id="acdefbc1-e346-45d6-b846-0c33e6a320d1",
        voice_id="145c6f3c-399a-4989-9761-8ba52cde0363",
        avatar_model="cara-4",
        industry="roofing",
        difficulty="Easy",
        trait_tags=[
            {"label": 'Plainspoken', "icon": '🗣️', "color": '#5b7fff'},
            {"label": 'Friendly', "icon": '😊', "color": '#ffb84d'},
            {"label": 'Time-pressed', "icon": '⏰', "color": '#ff5d7a'},
            {"label": 'Distrusts complexity', "icon": '🧩', "color": '#9b5cff'},
            {"label": 'ROI-focused', "icon": '💰', "color": '#4de8ff'},
        ],

        coaching_tips=[
            {"text": 'Keep it simple, he distrusts anything complicated', "icon": '🧩', "color": '#9b5cff'},
            {"text": "He's got crews on roofs, respect his time", "icon": '⏰', "color": '#ff5d7a'},
            {"text": 'Talk hours saved, not features', "icon": '💰', "color": '#4de8ff'},
            {"text": "No jargon, talk like you're on a job site", "icon": '🗣️', "color": '#5b7fff'},
        ],
    ),
    "monica_reyes": Persona(
        id="monica_reyes",
        name="Monica Reyes",
        role="Principal, Reyes Wealth Advisors (independent RIA, 3 advisors)",
        context=(
            "Runs a small independent wealth management practice. Fiduciary "
            "duty is central to how she thinks about everything, including "
            "software. Clients are older, high-net-worth, and would leave over "
            "a trust misstep. Compliance-minded to the point of caution."
        ),
        traits=(
            "Composed, formal, speaks in measured sentences. Not hostile to AI "
            "in principle but treats every claim as something that needs to "
            "survive a compliance review before she believes it."
        ),
        # From Anam's stock library via the Persona builder (persona
        # d1ffde27-4031-5042-8280-8b14a7ee23a9): stock avatar "Mina", stock
        # voice "Rachel - Polished Presence". As with Tom, the avatar ID is
        # the persona's `avatar.id` from GET /v1/personas/{id}, and the
        # persona itself (with its own prompt and Anam LLM) is not used for
        # sessions.
        avatar_id="65bdc796-f739-47fe-9a48-1134dc3ddae4",
        voice_id="90a1acd3-4fc0-11f1-84b0-52bacf74fa75",
        avatar_model="cara-4",
        industry="wealth_management",
        difficulty="Medium",
        trait_tags=[
            {"label": 'Composed', "icon": '🧘', "color": '#5b7fff'},
            {"label": 'Formal', "icon": '👔', "color": '#9b5cff'},
            {"label": 'Compliance-minded', "icon": '📜', "color": '#4de8ff'},
            {"label": 'Measured', "icon": '⚖️', "color": '#ffb84d'},
            {"label": 'Fiduciary-focused', "icon": '🏦', "color": '#ff5d7a'},
        ],

        coaching_tips=[
            {"text": 'Fiduciary duty frames everything for her', "icon": '🏦', "color": '#ff5d7a'},
            {"text": "She won't rush a decision, stay measured yourself", "icon": '⚖️', "color": '#ffb84d'},
            {"text": 'Compliance concerns need real answers, not reassurance', "icon": '📜', "color": '#4de8ff'},
            {"text": 'Formality matters, match her tone', "icon": '👔', "color": '#9b5cff'},
        ],
    ),
}

SCENARIOS: dict[str, Scenario] = {
    # --- Carla Diaz (restaurant) ---
    "carla_cost": Scenario(
        "carla_cost", "carla_diaz", "cost_roi", "What's This Actually Worth",
        "AI-assisted scheduling & inventory ordering",
        "I don't have room in the budget for a consultant right now. Show me "
        "the number where this pays for itself, not the sales pitch.",
        ["Fair, what's a bad ordering week actually costing you in waste or "
         "86'd menu items right now?",
         "If we cut food waste 8 percent across 3 locations, what's that a "
         "month, roughly?",
         "This isn't a subscription you hope pays off, it's built around your "
         "actual invoices from last quarter."],
        briefing=(
            "Carla Diaz reached out after hearing about AI-assisted scheduling "
            "and inventory tools for restaurants. You're two minutes into the "
            "pitch meeting, and she's already pushing back."
        ),
    ),
    "carla_adoption": Scenario(
        "carla_adoption", "carla_diaz", "adoption", "My Staff Won't Use It",
        "AI-assisted scheduling & inventory ordering",
        "My kitchen staff can barely agree on a group text. You really think "
        "they're going to learn some new AI system?",
        ["What do they actually use every day right now, texts, a whiteboard, "
         "both?",
         "If it worked inside the group text you already use, would that "
         "change your answer?",
         "It sits behind the tool your team already opens 40 times a shift, "
         "not a new app they have to remember."],
        briefing=(
            "Carla Diaz agreed to a follow-up meeting on AI-assisted scheduling "
            "and inventory tools. You're partway through the pitch, and she's "
            "just raised a concern about her kitchen staff."
        ),
    ),

    # --- Harold Bennett (law firm) ---
    "harold_accuracy": Scenario(
        "harold_accuracy", "harold_bennett", "trust_accuracy", "The Hallucination Problem",
        "AI-assisted document drafting & research support",
        "I've read the stories. Lawyers sanctioned for citing cases that don't "
        "exist. Why would I let that anywhere near my practice?",
        ["Those cases had one thing in common, nobody checked the citations "
         "before filing. What's your review process today?",
         "If every citation came with a verified source link before a human "
         "ever signed off, does that change the risk profile?",
         "This drafts a first pass under your review, the same as an "
         "associate's work, it doesn't file anything unchecked."],
        briefing=(
            "Harold Bennett took the meeting after a colleague recommended "
            "AI-assisted drafting and research tools for law firms. A few "
            "minutes in, he's raising concerns about accuracy."
        ),
    ),
    "harold_privacy": Scenario(
        "harold_privacy", "harold_bennett", "data_privacy", "Client Confidentiality",
        "AI-assisted document drafting & research support",
        "Where exactly does my client's privileged information go when I type "
        "it into your tool? Be specific, not reassuring.",
        ["What's your firm's current policy on where client documents can "
         "live, cloud storage, email, anywhere else?",
         "If nothing you enter is used to train any model and it's under a "
         "signed data processing agreement, is that the standard you need?",
         "Zero retention, zero training on your data, and a DPA before "
         "anything touches a client file. I'll get you that in writing."],
        briefing=(
            "You're midway through pitching AI-assisted drafting and research "
            "tools to Harold Bennett. He's just stopped you to ask where "
            "client data actually goes."
        ),
    ),

    # --- Priya Nair (e-commerce) ---
    "priya_already": Scenario(
        "priya_already", "priya_nair", "already_tried", "I Already Use ChatGPT",
        "AI-powered customer ops & content workflow",
        "I write my product descriptions and half my customer emails with "
        "ChatGPT already. What exactly am I paying you for?",
        ["What's still eating your time even with ChatGPT in the mix, what "
         "hasn't it solved?",
         "How much of your week goes to copy-pasting between ChatGPT, your "
         "store, and your inbox right now?",
         "You've got the raw tool. What you don't have is it wired into your "
         "store and inbox so it runs without you copy-pasting."],
        briefing=(
            "Priya Nair took the call out of curiosity about AI-powered "
            "customer ops tools. She's two minutes in and already comparing "
            "it to what she's using for free."
        ),
    ),
    "priya_trust": Scenario(
        "priya_trust", "priya_nair", "trust_accuracy", "Prove It's Better",
        "AI-powered customer ops & content workflow",
        "Every consultant says 'workflow' and 'automation.' Show me something "
        "concrete or this conversation's over.",
        ["What's the most annoying repeat task in your week, the one you'd "
         "cut first if you could?",
         "If I showed you your actual return-email backlog cleared in one "
         "pass, would that count as concrete?",
         "Here's your last 20 support emails answered in your voice, drafted "
         "in nine seconds. You approve or edit, nothing sends on its own."],
        briefing=(
            "You're partway into pitching AI-powered customer ops tools to "
            "Priya Nair. She's heard enough buzzwords for one call and wants "
            "proof."
        ),
    ),

    # --- Dr. Owens (dental) ---
    "owens_privacy": Scenario(
        "owens_privacy", "dr_owens", "data_privacy", "HIPAA, Not Hypothetically",
        "AI-assisted front-desk scheduling & patient communication",
        "The second patient data is involved, this is a HIPAA conversation, "
        "not a sales conversation. What's your actual compliance posture?",
        ["What's the biggest scheduling or no-show pain point your front desk "
         "deals with today?",
         "If everything ran under a signed Business Associate Agreement with "
         "zero data retention, would that clear your first bar?",
         "Signed BAA, encrypted in transit and at rest, no data used to train "
         "anything. That's the floor, not a feature."],
        briefing=(
            "Dr. Renee Owens agreed to hear about AI-assisted scheduling and "
            "patient communication tools. Minutes in, she's already focused "
            "on compliance."
        ),
    ),
    "owens_joblos": Scenario(
        "owens_joblos", "dr_owens", "job_loss", "My Staff Are Scared",
        "AI-assisted front-desk scheduling & patient communication",
        "A practice two miles from here automated their front desk and let go "
        "of two people. My staff have been here for over a decade. I'm not "
        "doing that to them.",
        ["What does a typical no-show or reschedule cost this practice in a "
         "week?",
         "If this took the after-hours phone tag off their plate instead of "
         "replacing them, does that change how it lands with your team?",
         "This handles the 9pm reschedule call nobody wants to take. Your "
         "front desk still runs the day, they just stop drowning in it."],
        briefing=(
            "You're partway through pitching AI-assisted scheduling tools to "
            "Dr. Renee Owens. She's just raised what's really on her mind, "
            "her front desk staff's jobs."
        ),
    ),

    # --- Tom Walsh (roofing) ---
    "walsh_time": Scenario(
        "walsh_time", "tom_walsh", "adoption", "I Don't Have Time to Learn Anything",
        "AI-assisted estimating & job-site photo documentation",
        "I don't have an hour to sit through training on some new system. "
        "I've got three crews on roofs right now.",
        ["Walk me through what happens today between a customer call and a "
         "quote going out.",
         "If there's nothing new to log into, it just drafts the quote from a "
         "photo your crew already takes, would ten minutes be worth it?",
         "No login, no dashboard to learn. Your crew keeps taking the same "
         "photos, the quote just shows up drafted instead of you writing it "
         "at 9pm."],
        briefing=(
            "Tom Walsh squeezed in ten minutes between job sites to hear "
            "about AI-assisted estimating tools. He's already eyeing the "
            "clock."
        ),
    ),
    "walsh_cost": Scenario(
        "walsh_cost", "tom_walsh", "cost_roi", "What Does This Actually Save Me",
        "AI-assisted estimating & job-site photo documentation",
        "Everything's a subscription these days. What am I actually getting "
        "back for what this costs me a month?",
        ["How many hours a week do you personally spend writing up quotes "
         "after a job walk?",
         "At your billable rate, what's five hours a week of your own time "
         "worth over a year?",
         "This gets you those five hours back. That's the whole pitch, "
         "everything else is detail."],
        briefing=(
            "You're partway through pitching AI-assisted estimating tools to "
            "Tom Walsh. He's cut to the only question he cares about, what "
            "does this actually save him."
        ),
    ),

    # --- Monica Reyes (wealth management) ---
    "reyes_fiduciary": Scenario(
        "reyes_fiduciary", "monica_reyes", "trust_accuracy", "Fiduciary Standard",
        "AI-assisted client research & meeting prep",
        "Everything I do carries a fiduciary duty. An AI tool being 'mostly "
        "right' isn't a standard I can operate under. Convince me otherwise.",
        ["What does your review process look like today before anything "
         "reaches a client?",
         "If this only assembles research and prep for you to review, never "
         "generates advice a client sees directly, does that fit your "
         "standard?",
         "It's a research assistant, not an advisor. Every output is drafted "
         "for your review, nothing reaches a client without you signing off."],
        briefing=(
            "Monica Reyes agreed to a meeting on AI-assisted research and "
            "meeting prep tools. She's a few minutes in and already framing "
            "everything around fiduciary duty."
        ),
    ),
    "reyes_privacy": Scenario(
        "reyes_privacy", "monica_reyes", "data_privacy", "Client Data Exposure",
        "AI-assisted client research & meeting prep",
        "My clients would leave if they knew their portfolio details touched "
        "some third-party AI tool without controls. Walk me through the "
        "actual exposure.",
        ["What's your current policy on where client portfolio data is "
         "allowed to live?",
         "If data stayed under a zero-retention agreement and never trained "
         "any model, would that meet what you'd need to disclose to clients?",
         "Zero retention, no training, and a data processing agreement your "
         "compliance team can actually review line by line before anything "
         "goes live."],
        briefing=(
            "You're midway through pitching AI-assisted research tools to "
            "Monica Reyes. She's just stopped to ask about client data "
            "exposure."
        ),
    ),
}


def get_persona(persona_id: str) -> Optional[Persona]:
    return PERSONAS.get(persona_id)


def get_scenario(scenario_id: str) -> Optional[Scenario]:
    s = SCENARIOS.get(scenario_id)
    if s and s.active:
        return s
    return None


def list_active_scenarios() -> list[Scenario]:
    return [s for s in SCENARIOS.values() if s.active]


def list_personas() -> list[Persona]:
    return list(PERSONAS.values())
