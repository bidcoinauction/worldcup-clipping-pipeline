# Brand System

## Purpose

Branding should be data, not source code. The current repository has hardcoded account positioning, hashtags, categories, and visual assumptions. These should eventually become organization or project configuration.

## Brand Inputs

Each organization or project should define:

- Brand name.
- Voice and tone.
- Colors.
- Fonts.
- Logo files.
- Safe area rules.
- Caption style.
- Hashtags.
- Platform notes.
- Calls to action.
- Sponsor rules.
- Forbidden language.
- Approval requirements.

## Current Repo Evidence

Current brand assumptions include:

- `config/pipeline_config.json` uses `America Discovers Football`.
- `scripts/generate_claude_prompt.py` is written for a US-targeted 2026 World Cup football account.
- `scripts/generate_asset_prompts.py` appends `#worldcup #football #soccer`.
- `FootballArchive/CLIPS/review_dashboard.html` uses Stadium Signal branding.

These are useful for the case study but should not define the general product.

## Brand Profile Concept

A future brand profile should be a portable config object:

```text
brand_id
organization_id
display_name
voice
caption_rules
hashtag_sets
export_profiles
logo_assets
platform_rules
approval_rules
```

## Platform Rules

Different outputs need different defaults:

- TikTok.
- Instagram Reels.
- YouTube Shorts.
- LinkedIn.
- X/Twitter.
- Internal review.

Early agency work can manage this manually with a brand brief. Later, repeated rules should become configuration.

## Human Review

Brand safety requires review before automation for early clients.

Review should answer:

- Is the clip accurate?
- Is the clip on brand?
- Is the caption safe?
- Is the crop acceptable?
- Is the rights status clear?
- Is the output ready to deliver?

## Recommended First Step

Create a manual brand intake template before implementing brand config.

The first technical move should be replacing hardcoded hashtags and account positioning with config only after a pilot validates the required fields.
