# Copyright, platform and accuracy compliance review

This is an engineering review of what the system enforces. It is **not legal advice**. Have a lawyer familiar with games and media review your footage sources, monetisation plans and channel branding before you launch.

## Intellectual property

**Built-in controls**
- **Rights register:** every asset needs an original source, owner, licence, usage permissions, allowed platforms and optional expiry. Assets are unusable until an admin verifies them, and any rights edit clears the verification.
- **Enforcement points:** rights are checked at footage selection, again at render time (licences can expire or be revoked), and again at QC per platform.
- **No acquisition of third-party video:** imports are limited to configured footage roots. There is no downloader. This repo's original `shorts_generator` downloads arbitrary YouTube videos; the engine does not use it.
- **Audio:** footage audio is never mixed into renders, because game audio may contain licensed music. Music and SFX must also be rights-registered.
- **Test-only assets:** the synthetic demo footage is marked "testing only" and QC blocks it from publishing.
- **Attribution:** `attribution_required` text is appended to platform descriptions automatically.
- **Thumbnails:** built from a frame of the channel's own rendered video plus original typography. The words "official/confirmed" are stripped unless the topic is officially confirmed, and speculation gets a "?".
- **Branding:** every description includes "Fan-made commentary. Not affiliated with or endorsed by Rockstar Games or Take-Two."

**Risks you must manage**
- GTA VI footage, trailers, logos and music are Take-Two/Rockstar property. "Permitted official promotional material" depends on the specific terms of each press kit or trailer. Record those terms in the licence field, and don't assume trailers are free to reuse.
- Rockstar publishes policies on fan content and video use of its games. Read the current versions and record your basis for use. Policies for unreleased games can differ from those for released ones.
- Adding narration does not make copyrighted footage lawful. Fair use and fair dealing are fact-specific and jurisdiction-dependent.
- Monetisation: Content ID and similar systems may claim revenue regardless of your rights position. The `monetization_allowed` flag records your position but cannot change platform behaviour.
- Brand name: the default brand "VI Signal" is a placeholder. Choose a name and visual identity that cannot be mistaken for Rockstar's.

## Accuracy and misinformation

- Five accuracy classes are derived from the evidence; an admin cannot override them (manual topics are classified from their sources).
- Unconfirmed material can never be filed as news. Theory and rumour scripts must state that plainly, or validation fails.
- Every factual claim must cite an attached source, and claims are checked against source text. Below the confidence threshold (default 0.8), the job is blocked and never published.
- Without a language model, scripts only reuse source sentences with attribution, so they cannot invent facts.
- **Known limit:** lexical fact checking can miss subtle misstatements. A configured model judge reduces this risk without removing it. Spot-check output, especially for news.

## Platform policies

| Platform | What the system does | What you must still do |
|----------|----------------------|------------------------|
| YouTube | Official Data API only; quota tracked; `selfDeclaredMadeForKids=false`; private test uploads | Pass Google's API compliance audit (unaudited projects can only upload private videos). Decide case by case on **altered/synthetic content disclosure**: the `containsSyntheticMedia` flag defaults to false because AI narration of an original script is not realistic depiction of a real person or event, but review YouTube's current guidance. Follow the YouTube Partner Program "reused content" and "inauthentic content" policies: original commentary and editing are essential |
| TikTok | Content Posting API; inbox drafts by default; direct post only with per-post human consent (privacy choice + Music Usage Confirmation); `is_aigc` set to true for AI narration | Complete TikTok's app audit for public direct posts; keep a human in the consent loop. Fully unattended public posting is not compatible with TikTok's posting guidelines, so the system does not do it |
| Instagram | Official API with a professional account; publishing-limit check; no private uploads in TEST mode | Meta App Review for the publishing permission; follow Branded Content and Community Guidelines |
| All | Per-platform daily limits, duplicate-content guard, backoff on rate limits, emergency pause | Watch for inauthentic or spam enforcement when posting many similar videos. Vary formats, and keep volume aligned with quality |

## AI voice

- The voice provider must supply a stock or designed voice that you are licensed to use. The system has no voice-cloning path.
- Descriptions disclose that narration uses AI text-to-speech.
