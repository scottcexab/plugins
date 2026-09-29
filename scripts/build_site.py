#!/usr/bin/env python3
# Copyright 2026 Exabeam, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generate the Exabeam Plug-in Catalog site from the data the catalog already holds. The design follows
www.exabeam.com: its announcement bar, top menu and footer, a light hero after /industries/financial-services/, and
the sub-menu under the hero that its product pages use.

    python3 scripts/build_site.py            # (re)write every page
    python3 scripts/build_site.py --check    # exit 1 if any page differs from what the sources produce, or a
                                             # generated page is no longer produced (CI)

Pages:
  index.html                          the front door; its plugin cards are discovered from plugins/*/index.html
  plugins/<name>/index.html           a plugin's detail page, generated for each catalog entry
  plugins/<name>/docs/[<slug>/]       the plugin's guide, rendered from its markdown, with a left-column index
  plugins/_template/index.html        a starter for a hand-authored page (not published: Jekyll skips "_" folders)

Cards: every plugins/<name>/index.html that carries a <script type="application/json" id="plugin-card"> block (and
no "draft": true) becomes a card, so a hand-authored page folder adds itself to the listing. Generated pages start
with a mark; a page without it is never overwritten.

Sources, nothing hand-written per plugin:
  .claude-plugin/marketplace.json     the entries: name, displayName, description, category, keywords
  vendor.lock.json                    the blessed build per entry: version, sha, release, date, who blessed it
  <entry>/identity.json               the payload's own identity: shortDescription, hostKeywords, license
  <entry>/skills/*/SKILL.md           each skill's frontmatter: name + description (the invocation phrases are
                                      the quoted "…" fragments the description lists)
  <entry>/README.md                   the detail page's long-form content: "What it does" (bullets), the skills
                                      table, "Get started" (numbered steps), "Documentation" (the doc pages, in
                                      order) and "Status". Any section it lacks is left out.
  <entry>/docs/index.md + the docs    the doc pages' content
  site/<entry>.json                   optional, site-only facts the payload does not carry: "status" (the card's
                                      release pill, "pre-release" or "released"; read from the README's Status
                                      section when absent), "card_description" (the card's text, up to 400
                                      characters; the catalog description, fitted to 400, when absent) and the
                                      detail page's images, "media": [{"src", "alt", "caption"}], src relative to
                                      the repo root.
"""
import base64
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "index.html"
SITE_DIR = ROOT / "site"                       # site/<name>.json — site-only media per plugin
CATALOG = ROOT / ".claude-plugin" / "marketplace.json"
LOCK = ROOT / "vendor.lock.json"
LOGO = ROOT / "assets" / "exabeam-logo.svg"
TERMS_URL = "https://www.exabeam.com/legal/enterprise-agreement/"
COMMUNITY_URL = "https://open-agent-ai-security.github.io/"
REPO_URL = "https://github.com/Exabeam-Labs/plugins"

HOST_LABELS = {"claude": "Claude Code", "codex": "OpenAI Codex"}
h = html.escape


def frontmatter(text):
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return {}
    fm, out, key, buf = m.group(1), {}, None, []
    for line in fm.splitlines():
        km = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if km and not line.startswith(" "):
            if key:
                out[key] = " ".join(x.strip() for x in buf).strip()
            key, first = km.group(1), km.group(2).strip()
            buf = [] if first in (">-", ">", "|", "|-") else [first]
        elif key is not None:
            buf.append(line)
    if key:
        out[key] = " ".join(x.strip() for x in buf).strip()
    return out


def first_sentence(text):
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


def invocations(description, limit=3):
    """The quoted phrases a skill's description lists as ways to ask for it."""
    seen, out = set(), []
    for q in re.findall(r'"([^"]{4,60})"', description):
        q = q.strip()
        if q.lower() not in seen:
            seen.add(q.lower()); out.append(q)
        if len(out) >= limit:
            break
    return out




# ---------- the payload README, as the detail page's long-form source ----------

# ---------- front-door copy, shared by every landing-page design (index.html, and any prototype under home2/) ----------
HERO_LEAD = "Add Exabeam's knowledge and capabilities to the AI agent of your choice, on your terms. Bring your own AI, your choice of model, agent harness and data sovereignty, and put Exabeam's products to work inside it."
ABOUT_INTRO = "The Exabeam Supported Plugin Marketplace is Exabeam's official catalog of supported plugins for AI CLI environments such as Claude Code and OpenAI Codex. Each plugin extends what Exabeam can do by packaging product knowledge, workflows, and integrations as skills that security practitioners can invoke directly from the agent tools they already use."
PILLARS = [
    ("Capabilities in the hands of practitioners", "Analysts, detection engineers, and SOC leads don't have to wait for new features to reach a console. They can install a plugin in minutes and put Exabeam-aware skills to work on everyday tasks like investigating alerts, triaging cases, tuning detections, and more."),
    ("Supported by Exabeam", "Every plugin in the marketplace is built, tested, and maintained by Exabeam. It is versioned against Exabeam releases and backed by Exabeam support under your existing agreement, so teams can adopt agent-driven workflows with confidence."),
    ("Your model, your harness, your data", "Plugins run in the agent environment you choose, against your own Exabeam tenant, so you keep control over model selection and data sovereignty."),
    ("Human in the loop", "Plugins are designed so consequential actions stay under practitioner control. Approvals are required before state-changing operations, and containment is recommended rather than executed automatically."),
]


def text(s):
    """Escape for a text node: apostrophes stay apostrophes."""
    return html.escape(s, quote=False)


def readme_sections(text):
    """{heading: body} for every level-2 heading; HTML comments dropped."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out, key, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if key: out[key] = "\n".join(buf).strip()
            key, buf = m.group(1).strip(), []
        elif key is not None:
            buf.append(line)
    if key: out[key] = "\n".join(buf).strip()
    return out


def list_items(body, marker):
    """The items of a markdown list (marker: '-' or a numbered one), continuation lines joined."""
    pat = r"^\s*-\s+" if marker == "-" else r"^\s*\d+\.\s+"
    items = []
    for line in body.splitlines():
        if re.match(pat, line):
            items.append(re.sub(pat, "", line).strip())
        elif items and line.strip() and line.startswith(" "):
            items[-1] += " " + line.strip()
        elif items and not line.strip():
            continue
        elif items:
            break
    return items


def table_rows(body):
    rows = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    return [c for c in cells[1:] if not all(re.fullmatch(r":?-+:?", x) for x in c)]   # drop header + rule


def paragraphs(body):
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip() and not p.lstrip().startswith(("|", ">", "-", "#", "```"))]


def rel(href):
    return href[2:] if href.startswith("./") else href


def lead_in(text):
    """A highlight is "**Lead** rest": drop the dash or colon joining them, since the page sets the lead on its own line."""
    return re.sub(r"^(\*\*[^*]+\*\*)\s*[\u2014:,-]\s*", r"\1 ", text)


def md_inline(text, base):
    """The inline markdown a README uses — `code`, **bold**, *italic*, [text](link) — as escaped HTML.
    Relative links resolve to the file on GitHub under the payload directory `base`."""
    codes = []
    def keep(m):
        codes.append(f"<code>{html.escape(m.group(1))}</code>"); return f"\x00{len(codes) - 1}\x00"
    s = re.sub(r"`([^`]+)`", keep, text)
    s = html.escape(s, quote=False)
    def link(m):
        label, href = m.group(1), m.group(2)
        if not re.match(r"[a-z]+:", href):
            href = f"{REPO_URL}/blob/main/{base}/{rel(href)}"
        return f'<a href="{html.escape(href)}">{label}</a>'
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


def strip_emoji(text):
    return re.sub(r"^[^\w*`\[(\"“]+", "", text).strip()


def in_catalog_voice(text, project, name_as="the plugin"):
    """The site presents each plugin as an Exabeam catalog entry, not as the upstream project it is built from:
    the project's own name reads as "the plugin" (or the plugin's display name, where prose needs a proper noun)."""
    return re.sub(rf"(?<![/\w.-]){re.escape(project)}(?![\w-]|/)", name_as, text) if project else text   # prose, never a URL


def own_sentences(text):
    """Only the sentences that link nowhere outside this repository (the upstream README's status points at its
    own repository's badges, changelog and security runs)."""
    keep = [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
            if all(h.startswith(REPO_URL) or not re.match(r"[a-z]+:", h) and not h.startswith("..") for h in re.findall(r"\]\(([^)\s]+)\)", x))
            and "badge" not in x]
    return " ".join(keep)


def readme_content(pdir, name, skill_names, project=""):
    path = pdir / "README.md" if pdir else None
    if not path or not path.exists():
        return {}
    sec = readme_sections(in_catalog_voice(path.read_text(encoding="utf-8"), project))
    get = lambda *keys: next((sec[k] for k in sec for want in keys if k.lower() == want), "")
    doc = {"highlights": [md_inline(lead_in(strip_emoji(i)), name) for i in list_items(get("what it does"), "-")],
           "steps": [md_inline(i, name) for i in list_items(get("get started", "getting started", "quick start"), "1")],
           "status": [md_inline(t, name) for t in (own_sentences(p) for p in paragraphs(get("status"))) if t],
           "roles": {}, "docs": []}
    for body in sec.values():                      # the skills table: first column names each skill
        for row in table_rows(body):
            m = re.search(r"`([^`]+)`", row[0]) if row else None
            if m and m.group(1) in skill_names and len(row) >= 3:
                doc["roles"][m.group(1)] = {"who": md_inline(row[1], name), "what": md_inline(row[-1], name)}
    for row in table_rows(get("documentation")):
        m = re.search(r"\[([^\]]+)\]\(([^)\s]+)\)", row[0]) if row else None
        if m:
            href = m.group(2) if re.match(r"[a-z]+:", m.group(2)) else f"{REPO_URL}/blob/main/{name}/{rel(m.group(2))}"
            doc["docs"].append({"title": m.group(1), "href": href, "what": md_inline(row[1], name) if len(row) > 1 else ""})
    return doc


def load():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    entries = []
    for e in catalog["plugins"]:
        name = e["name"]
        src = e.get("source")
        pdir = ROOT / src[2:] if isinstance(src, str) and src.startswith("./") else None
        ident = json.loads((pdir / "identity.json").read_text(encoding="utf-8")) if pdir and (pdir / "identity.json").exists() else {}
        skills = []
        if pdir and (pdir / "skills").is_dir():
            for sk in sorted((pdir / "skills").glob("*/SKILL.md")):
                fm = frontmatter(sk.read_text(encoding="utf-8"))
                desc = fm.get("description", "")
                skills.append({"name": fm.get("name") or sk.parent.name, "summary": first_sentence(desc), "ask": invocations(desc)})
        hosts = []
        if pdir:
            if (pdir / ".claude-plugin" / "plugin.json").exists(): hosts.append("claude")
            if (pdir / ".codex-plugin" / "plugin.json").exists(): hosts.append("codex")
        # Order the skills the way the entry's own description introduces them; alphabetical for the rest.
        desc = e.get("description", "")
        def by_mention(s):                      # skills in the order the entry description names them
            i = desc.find(s["name"])
            return (i if i >= 0 else 10**6, s["name"])
        skills.sort(key=by_mention)
        rec = lock.get(name, {})
        site = SITE_DIR / f"{name}.json"
        site_cfg = json.loads(site.read_text(encoding="utf-8")) if site.exists() else {}
        media = site_cfg.get("media", [])
        for m in media:
            if not (ROOT / m["src"]).is_file():
                raise SystemExit(f"{site.relative_to(ROOT)}: media src {m['src']!r} does not exist")
        upstream = re.sub(r"\.git$", "", rec.get("upstream", ""))
        entries.append({
            "name": name, "display": e.get("displayName") or name, "description": e.get("description", ""),
            "category": e.get("category", ""), "license": e.get("license", ""), "short": ident.get("shortDescription", ""),
            "version": rec.get("version") or ident.get("version", ""), "sha": rec.get("sha") or "",
            "blessed": rec.get("vendored", ""), "blessed_by": rec.get("blessed_by", ""), "release": rec.get("release", ""),
            "upstream": upstream, "hosts": hosts, "skills": skills, "media": media, "site_status": site_cfg.get("status", ""),
            "card_description": site_cfg.get("card_description", ""),
            "readme": readme_content(pdir, name, {s["name"] for s in skills}, upstream.rsplit("/", 1)[-1] if upstream else ""),
            "docs": f"{name}/README.md" if pdir and (pdir / "README.md").exists() else "",
            "setup": f"{name}/docs/installation.md" if pdir and (pdir / "docs" / "installation.md").exists() else "",
        })
    return catalog, entries


def svg_uri(svg):
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def logos():
    """Header: the full-colour logo on white. Footer: the same mark with the wordmark in white, as agentsensor.exabeam.com does."""
    svg = LOGO.read_text(encoding="utf-8")
    return svg_uri(svg), svg_uri(svg.replace(".cls-3{fill:#000;}", ".cls-3{fill:#fff;}"))


# The design system is agentsensor.exabeam.com's (itself aligned to exabeam.com): Lausanne with an Inter fallback,
# JetBrains Mono for code, Exabeam green #009d00 / blue #1863dc, pill buttons, white sticky header, dark footer.
SOCIAL = """<a href="https://www.linkedin.com/company/exabeam/" target="_blank" rel="noopener" aria-label="LinkedIn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/></svg></a>
<a href="https://x.com/exabeam" target="_blank" rel="noopener" aria-label="X"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></a>
<a href="https://www.instagram.com/exabeam/" target="_blank" rel="noopener" aria-label="Instagram"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="0.6" fill="currentColor" stroke="none"/></svg></a>
<a href="https://www.youtube.com/c/ExabeamSIEM" target="_blank" rel="noopener" aria-label="YouTube"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="3"/><polygon points="10,9 16,12 10,15" fill="currentColor" stroke="none"/></svg></a>"""


# ---------- footer: www.exabeam.com's own footer, text and links verbatim, plus a Plug-in Catalog row ----------
EXABEAM = "https://www.exabeam.com"
FOOTER_COLUMNS = [
    ("About Exabeam", "g", [("About Us", "/company/about-us/"), ("Leadership", "/company/about-us/#leadership"),
                            ("Industry Awards", "/newsroom/?type=awards"), ("Analyst Reports", "/company/analyst-corner/"),
                            ("Newsroom", "/newsroom/"), ("Media Kit", "/newsroom/media-kit/"), ("Events", "/events/"),
                            ("Careers", "/company/careers/")]),
    ("Popular Links", "b", [("The Exabeam Difference", "/platform/the-exabeam-difference/"), ("What’s New at Exabeam", "/whats-new/"),
                            ("Blog", "/blog/"), ("Resource Library", "/resources/"), ("Customer Stories", "/customers/"),
                            ("The New CISO Podcast", "/the-new-ciso-podcast/"), ("Exabeam Trust Center", "https://exabeam.securitypal.com/"),
                            ("Find a Partner", "/find-a-partner/")]),
    ("Get Support", "p", [("Support and Services", "/support-and-services/"),
                          ("Professional Services", "/support-and-services/professional-services/"),
                          ("Education and Training", "/support-and-services/education-and-training/"),
                          ("Documentation", "https://docs.exabeam.com/"), ("Community", "https://mycommunity.exabeam.com/"),
                          ("GitHub", "https://github.com/Exabeam"), ("Service Status", "https://exabeam.statuspage.io/")]),
]
FOOTER_EXPLAINERS = [
    ("SIEM", "/explainers/siem/what-is-siem/"), ("SIEM Tools", "/explainers/siem-tools/siem-buyers-guide/"),
    ("UEBA", "/explainers/ueba/what-ueba-stands-for-and-a-5-minute-ueba-primer/"),
    ("Information Security", "/explainers/information-security/information-security-goals-types-and-applications/"),
    ("AI Cyber Security", "/explainers/ai-cyber-security/ai-cyber-security-securing-ai-systems-against-cyber-threats/"),
    ("Log Management", "/explainers/log-management/log-management-process-tools-and-tips-for-success/"),
    ("SOAR", "/explainers/soar/how-soar-works-pros-cons-and-choosing-the-right-platform/"),
    ("Insider Threats", "/explainers/insider-threats/insider-threats/"),
    ("MITRE ATT&CK", "/explainers/mitre-attck/what-is-mitre-attck-an-explainer/"),
    ("Compliance Management", "/explainers/compliance-management/compliance-management-process-regulations-and-tools/"),
    ("PCI Compliance", "/explainers/pci-compliance/pci-compliance-a-quick-guide/"),
    ("GDPR Compliance", "/explainers/gdpr-compliance/gdpr-compliance-a-practical-guide/"),
    ("HIPAA Compliance", "/explainers/hipaa-compliance/what-is-the-hipaa-compliance-standard-and-how-to-adhere-to-it/"),
    ("SOX Compliance", "/explainers/sox-compliance/sox-compliance-requirements-and-checklist/"),
    ("Network Detection and Response", "/explainers/network-detection-and-response/network-detection-and-response-capabilities-alternatives/"),
    ("What Are TTPs", "/explainers/what-are-ttps/what-are-ttps-and-how-understanding-them-can-help-prevent-the-next-incident/"),
    ("New-Scale SIEM", "/explainers/new-scale-siem/10-must-have-features-to-be-a-modern-siem/"),
    ("SIEM Security", "/explainers/siem-security/a-siem-security-primer/"), ("Event Logging", "/explainers/event-logging/event-log/"),
    ("Cloud Security", "/explainers/cloud-security/cloud-security-principles-solutions-and-architectures/"),
    ("XDR", "/explainers/xdr/what-is-xdr-transforming-threat-detection-and-response/"),
]
FOOTER_LEGAL = [("Terms and Conditions", "/legal/terms-and-conditions/"), ("Privacy Policy", "/legal/privacy-policy/"),
                ("Ethical Trading Policy", "/legal/ethical-trading-policy/")]

# ---------- header: www.exabeam.com's top menu, verbatim (items, panel intros, columns, links) ----------
NAV_MENUS = [
    ("Products", "The Exabeam Product Portfolio",
     "Whether it’s insider threat defense for users and agents, securing AI adoption across your enterprise. or advanced threat detection, investigation, and response (TDIR) powered by AI and automation, Exabeam has you covered.",
     [("Capabilities", [("Agent Behavior Analytics (ABA)", "/capabilities/agent-behavior-analytics/"),
                        ("Threat Detection, Investigation, and Response (TDIR)", "/capabilities/tdir/"), ("SIEM", "/capabilities/siem/"),
                        ("UEBA", "/capabilities/ueba/"), ("SOAR", "/capabilities/soar/"), ("AI", "/company/ai-innovation/"),
                        ("Insider Threats", "/capabilities/insider-threats/"), ("Compliance", "/capabilities/compliance/")]),
      ("Cloud-Native Platform", [("New-Scale Fusion", "/platform/exabeam-new-scale-fusion-security-operations-platform/"),
                                 ("New-Scale SIEM", "/platform/new-scale-siem/"), ("New-Scale Analytics", "/platform/new-scale-analytics/"),
                                 ("Exabeam Nova AI Agent", "/platform/exabeam-nova/"), ("Outcomes Navigator", "/platform/outcomes-navigator/"),
                                 ("NetMon", "/platform/netmon/")]),
      ("Self-Hosted Platform", [("LogRhythm SIEM", "/platform/logrhythm-siem/"), ("LogRhythm Intelligence", "/platform/logrhythm-intelligence/"),
                                ("NetMon", "/platform/netmon/")])], None),
    ("Solutions", "Exabeam Solutions",
     "Meet your strategic security outcomes with Exabeam. From industry-specific requirements to outcome-focused use case coverage, Exabeam delivers faster, more accurate threat detection, investigation, and response (TDIR).",
     [("By Use Case", [("Insider Threats", "/use-cases/insider-threats/"), ("External Threats", "/use-cases/external-threats/"),
                       ("Compliance", "/use-cases/compliance/")]),
      ("By Industry", [("Financial Services", "/industries/financial-services/"), ("Government", "/industries/government/"),
                       ("Healthcare", "/industries/healthcare/"), ("Higher Education", "/industries/higher-education/"),
                       ("Manufacturing", "/industries/manufacturing/")]),
      ("Benefits", [("Secure the Agentic Enterprise", "/company/ai-innovation"), ("Detect High-Risk Threats", "/benefits/detect-threats/"),
                    ("Automate Investigation & Response", "/benefits/automate-investigation-response/"),
                    ("Improve Threat Coverage", "/benefits/improve-threat-coverage/"),
                    ("Optimize Security Investments", "/benefits/optimize-security-investments/"),
                    ("Flexible Deployment", "/benefits/flexible-deployment/")])], None),
    ("Resources", "Resources",
     "Learn more about Exabeam and the latest trends in security operations, browse the resource library, attend an event near you, or explore our Explainers to get up to speed on industry topics.",
     [("Support & Services", [("Professional Services", "/support-and-services/professional-services/"),
                              ("Education & Training", "/support-and-services/education-and-training/"),
                              ("Community", "https://mycommunity.exabeam.com/"), ("Documentation", "https://docs.exabeam.com/")]),
      ("Learning Resources", [("Blog", "/blog/"), ("Resource Library", "/resources/"), ("The New CISO Podcast", "/the-new-ciso-podcast/"),
                              ("Events", "/events/"), ("Webinars", "https://eventhub.exabeam.com/"), ("Newsroom", "/newsroom/"),
                              ("TEN18 by Exabeam", "/ten18/")]),
      ("Security Explainers", [("SIEM", "/explainers/siem/what-is-siem/"), ("SIEM Tools", "/explainers/siem-tools/siem-buyers-guide/"),
                               ("UEBA", "/explainers/ueba/what-ueba-stands-for-and-a-5-minute-ueba-primer/"),
                               ("Information Security", "/explainers/information-security/information-security-goals-types-and-applications/"),
                               ("Cyber Threat Intelligence", "/explainers/cyber-threat-intelligence/4-types-of-cyber-threat-intelligence-and-using-them-effectively/"),
                               ("AI Cyber Security", "/explainers/ai-cyber-security/ai-cyber-security-securing-ai-systems-against-cyber-threats/"),
                               ("Log Management", "/explainers/log-management/log-management-process-tools-and-tips-for-success/"),
                               ("SOAR", "/explainers/soar/how-soar-works-pros-cons-and-choosing-the-right-platform/"),
                               ("Insider Threats", "/explainers/insider-threats/insider-threats/")])], ("View All", "/explainers/")),
    ("Why Exabeam", "Why Exabeam",
     "Exabeam helps the world’s leading organizations fight what they can’t see with faster, more accurate, and repeatable threat detection, investigation, and response (TDIR).",
     [("Leading with Value", [("AI Innovation", "/company/ai-innovation/"), ("What’s New at Exabeam", "/whats-new/"),
                              ("Customer Stories", "/customers/"), ("Trusted & Secure", "/company/trusted-and-secure/"),
                              ("Awards", "/newsroom/?type=awards"), ("Analyst Reports", "/company/analyst-corner/"),
                              ("The Exabeam Difference", "/platform/the-exabeam-difference/")]),
      ("Trusted Partners", [("Partner Program", "/partner-overview/"), ("Find a Partner", "/find-a-partner/"),
                            ("Become a Partner", "https://partners.exabeam.com/#/page/become-a-partner"),
                            ("Partner Portal", "https://partners.exabeam.com/")]),
      ("People & Careers", [("Meet the Team", "/company/about-us/"), ("Diversity, Inclusion & Culture", "/company/diversity/"),
                            ("Join Us", "/company/careers/")])], None),
]

HEADER_CSS = """
.xa{background:linear-gradient(90deg,#009d00 0%,#00897a 55%,#006bff 100%);color:#fff;font-size:15px;font-weight:500;line-height:1.4}
.xa .xa-in{max-width:1168px;margin:0 auto;padding:0 20px;display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:55px}
.xa a{color:#fff;font-weight:600;text-decoration:underline}
.xa button{flex:none;width:22px;height:22px;border:2px solid #fff;border-radius:3px;background:none;color:#fff;font:700 12px/1 sans-serif;cursor:pointer;padding:0}
.xh{position:relative;z-index:60;background:#fff;font-size:16px;line-height:1.4}
.xh a{text-decoration:none}.xh a:hover{text-decoration:none}
.xh-in{max-width:1296px;margin:0 auto;padding:0 24px;height:95px;display:flex;align-items:center;gap:32px}
.xh-logo img{height:28px;display:block}
.xh-menu{flex:1;display:flex;align-items:center}
.xh-menu>ul{list-style:none;margin:0;padding:0;display:flex;gap:44px;margin-right:auto}
.xh-top{font:inherit;font-weight:700;color:#000;background:none;border:none;padding:8px 0;cursor:pointer;position:relative}
.xh-top::after{content:'';position:absolute;left:0;right:0;bottom:0;height:2px;background:#009d00;transform:scaleX(0);transition:transform .15s}
.xh-item:hover .xh-top,.xh-item.open .xh-top{color:#009d00}
.xh-item:hover .xh-top::after,.xh-item.open .xh-top::after{transform:scaleX(1)}
.xh-panel{display:none;position:absolute;left:0;right:0;top:100%;background:#fff;border-top:1px solid #e5e5e5;box-shadow:0 12px 24px rgba(0,0,0,.12)}
.xh-item.open .xh-panel{display:block}
@media(hover:hover) and (min-width:1101px){.xh-item:hover .xh-panel{display:block}}
.xh-panel-in{max-width:1168px;margin:0 auto;padding:40px 20px 44px;display:grid;grid-template-columns:repeat(4,1fr);gap:28px}
.xh-intro h3{font-size:21px;line-height:1.3;font-weight:800;color:#000;margin:0 0 8px}
.xh-intro p{font-size:14px;line-height:1.7;color:#000;margin:0}
.xh-col h4{font-size:14.5px;font-weight:700;color:#009d00;margin:0 0 14px;padding-bottom:14px;border-bottom:1px solid #cecece}
.xh-col a{display:block;font-size:14.5px;font-weight:700;color:#000;margin-bottom:12px;line-height:1.35}
.xh-col a:hover{color:#009d00}
.xh-col a.all{font-size:13px;font-weight:400;color:#808080;margin-top:6px}
.xh-util{display:flex;align-items:center;gap:36px}
.xh-icon{background:none;border:none;padding:4px;cursor:pointer;color:#000;display:flex}
.xh-lang{position:relative}
.xh-lang>button{font:inherit;font-weight:700;color:#000;background:none;border:none;cursor:pointer;display:flex;align-items:center;gap:6px;padding:4px 0}
.xh-lang ul{display:none;position:absolute;right:-12px;top:100%;margin:10px 0 0;padding:10px 0;list-style:none;background:#fff;box-shadow:0 8px 24px rgba(0,0,0,.14);min-width:140px}
.xh-lang.open ul{display:block}.xh-lang li{padding:6px 18px;font-weight:700;color:#009d00}
.xh-util>a{font-weight:700;color:#000}.xh-util>a:hover{color:#009d00}
.xh-search{display:none;position:absolute;left:0;right:0;top:100%;background:#fff;border-top:1px solid #e5e5e5;box-shadow:0 12px 24px rgba(0,0,0,.12)}
.xh.searching .xh-search{display:block}
.xh-search form{max-width:1168px;margin:0 auto;padding:24px 20px}
.xh-search input{width:100%;font:700 16px/1.4 inherit;font-family:inherit;padding:12px 14px;border:1px solid #808080;color:#000}
.xh-demo{flex:none;background:#ffc000;border:1.5px solid #ffc000;color:#000;font-weight:700;font-size:18px;border-radius:40px;padding:9px 19px 8px;white-space:nowrap}
.xh-demo:hover{background:#fff}
.xh-burger{display:none;background:none;border:none;padding:6px;cursor:pointer;margin-left:auto}
.xh-burger span{display:block;width:24px;height:2px;background:#000;margin:5px 0}
@media(max-width:1100px){
.xh-in{gap:16px}.xh-burger{display:block}.xh-demo{order:3}
.xh-menu{display:none;position:absolute;left:0;right:0;top:100%;background:#fff;flex-direction:column;align-items:stretch;padding:8px 24px 24px;box-shadow:0 12px 24px rgba(0,0,0,.12);max-height:calc(100vh - 150px);overflow:auto}
.xh.open .xh-menu{display:flex}
.xh-menu>ul{flex-direction:column;gap:0;margin:0}
.xh-item{border-bottom:1px solid #e5e5e5}.xh-top{width:100%;text-align:left;padding:16px 0}.xh-top::after{display:none}
.xh-panel{position:static;box-shadow:none;border:none}.xh-panel-in{grid-template-columns:1fr;padding:0 0 18px;gap:18px}.xh-intro{display:none}
.xh-util{flex-wrap:wrap;gap:20px;padding-top:18px}.xh-lang ul{right:auto;left:0}}
@media(max-width:560px){.xh-in{height:72px;padding:0 16px}.xh-demo{font-size:15px;padding:8px 14px 7px}.xa{font-size:13.5px}}
/* sub-menu under the hero, as on www.exabeam.com product pages */
.xs{position:sticky;top:0;z-index:40;background:#fff;border-top:1px solid #cecece;border-bottom:1px solid #cecece}
.xs-in{max-width:var(--xs-max,1168px);margin:0 auto;padding:26px var(--xs-pad,20px);display:flex;flex-wrap:wrap;gap:12px 48px;line-height:1.4}
.xs a{position:relative;font-size:16px;font-weight:700;color:#000;text-decoration:none}
.xs a:hover{color:#009d00;text-decoration:none}
.xs a.active::after{content:'';position:absolute;left:0;right:0;bottom:-6px;height:3px;background:#009d00}
@media(max-width:640px){.xs-in{padding:18px 16px;gap:10px 24px}.xs a{font-size:15px}}
"""

HEADER_JS = """(()=>{const xh=document.querySelector('.xh');if(!xh)return;
const closeAll=except=>xh.querySelectorAll('.xh-item.open,.xh-lang.open').forEach(i=>{if(i!==except){i.classList.remove('open');const b=i.querySelector('button');b&&b.setAttribute('aria-expanded','false')}});
xh.querySelectorAll('.xh-item>.xh-top,.xh-lang>button').forEach(b=>b.addEventListener('click',e=>{const li=b.parentElement;const on=!li.classList.contains('open');closeAll(li);xh.classList.remove('searching');li.classList.toggle('open',on);b.setAttribute('aria-expanded',on);e.stopPropagation()}));
xh.querySelector('.xh-burger').addEventListener('click',e=>{const on=xh.classList.toggle('open');e.currentTarget.setAttribute('aria-expanded',on)});
xh.querySelector('.xh-find').addEventListener('click',e=>{closeAll();const on=xh.classList.toggle('searching');if(on)xh.querySelector('.xh-search input').focus();e.stopPropagation()});
document.addEventListener('click',e=>{if(!xh.contains(e.target)){closeAll();xh.classList.remove('searching')}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeAll();xh.classList.remove('searching','open')}});
const xa=document.querySelector('.xa button');xa&&xa.addEventListener('click',()=>xa.closest('.xa').remove());
const links=[...document.querySelectorAll('.xs a')];if(!links.length)return;
const pairs=links.map(a=>[a,document.querySelector(a.getAttribute('href'))]).filter(p=>p[1]);
let queued=false;const mark=()=>{queued=false;let cur=pairs[0];
for(const p of pairs){if(p[1].getBoundingClientRect().top<=140)cur=p}
if(innerHeight+scrollY>=document.documentElement.scrollHeight-2)cur=pairs[pairs.length-1];
links.forEach(a=>a.classList.toggle('active',a===cur[0]))};
addEventListener('scroll',()=>{if(!queued){queued=true;requestAnimationFrame(mark)}},{passive:true});mark();})();"""

SEARCH_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="7"/><path d="m20 20-4.5-4.5"/></svg>'
CHEVRON = '<svg width="12" height="8" viewBox="0 0 12 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m1 1 5 5 5-5"/></svg>'


def header(announce=""):
    """www.exabeam.com's announcement bar and top menu: Products / Solutions / Resources / Why Exabeam panels,
    search, language, Contact Us and Get a Demo. Every link points at www.exabeam.com, as it does there."""
    h = html.escape
    logo, _ = logos()
    url = lambda u: u if u.startswith("http") else EXABEAM + u
    items = ""
    for i, (label, title, intro, cols, more) in enumerate(NAV_MENUS):
        colhtml = ""
        for j, (head, links) in enumerate(cols):
            extra = f'<a class="all" href="{h(url(more[1]))}">{h(more[0])}</a>' if more and j == len(cols) - 1 else ""
            anchors = "".join(f'<a href="{h(url(u))}">{h(t)}</a>' for t, u in links)
            colhtml += f'<div class="xh-col"><h4>{h(head)}</h4>{anchors}{extra}</div>'
        items += (f'<li class="xh-item"><button class="xh-top" type="button" aria-expanded="false" aria-controls="xh-p{i}">{h(label)}</button>'
                  f'<div class="xh-panel" id="xh-p{i}"><div class="xh-panel-in"><div class="xh-intro"><h3>{h(title)}</h3><p>{h(intro)}</p></div>{colhtml}</div></div></li>')
    bar = f'<div class="xa"><div class="xa-in"><span>{announce}</span><button type="button" aria-label="Dismiss">✕</button></div></div>' if announce else ""
    return f"""{bar}
<header class="xh"><div class="xh-in">
  <a class="xh-logo" href="{EXABEAM}/" aria-label="Exabeam"><img src="{logo}" alt="Exabeam"></a>
  <button class="xh-burger" type="button" aria-label="Menu" aria-expanded="false" aria-controls="xh-menu"><span></span><span></span><span></span></button>
  <nav class="xh-menu" id="xh-menu" aria-label="Main">
    <ul>{items}</ul>
    <div class="xh-util">
      <button class="xh-icon xh-find" type="button" aria-label="Search">{SEARCH_ICON}</button>
      <div class="xh-lang"><button type="button" aria-expanded="false">EN {CHEVRON}</button><ul><li>English</li></ul></div>
      <a href="{EXABEAM}/contact/">Contact Us</a>
    </div>
  </nav>
  <a class="xh-demo" href="{EXABEAM}/contact/get-a-demo/">Get a Demo</a>
</div>
<div class="xh-search"><form action="{EXABEAM}/" method="get" role="search"><input type="text" name="s" placeholder="Search" aria-label="Search exabeam.com" required></form></div>
</header>"""


def subnav(items):
    """The white sub-menu under the hero on www.exabeam.com product pages: links to sections of this page."""
    return '<nav class="xs" aria-label="On this page"><div class="xs-in">' + "".join(
        f'<a href="#{html.escape(i)}">{html.escape(t)}</a>' for i, t in items) + "</div></nav>"


def announcement(entries, root):
    e = entries[0] if entries else None
    return (f'<strong>{html.escape(e["display"])}</strong> is in the catalog, <a href="{root}plugins/{html.escape(e["name"])}/">View details</a>'
            if e else "")


FOOTER_CSS = """
.xf{font-family:inherit;margin-top:0}
.xf::before{content:'';display:block;height:12px;background:linear-gradient(90deg,#009d00,#00897a 55%,#006bff)}
.xf a{text-decoration:none}.xf a:hover{text-decoration:none}
.xf .xw{max-width:1144px;margin:0 auto;padding:0 24px}
.xf-top{background:#313131;color:#fff;padding:84px 0 60px}
.xf-top .xw{display:grid;grid-template-columns:repeat(4,1fr);gap:24px}
.xf-brand img{height:34px;display:block;margin-bottom:34px}
.xf-brand p{margin:0 0 22px;font-size:17px;line-height:1.6;font-weight:500;color:#fff}.xf-brand p a{color:#fff}
.xf-social{display:flex;align-items:center;gap:16px;font-size:17px;font-weight:500;margin-bottom:30px}
.xf-social .icons{display:flex;gap:16px}.xf-social .icons a{color:#fff}.xf-social svg{width:20px;height:20px;display:block}
.xf-ctas{display:flex;flex-direction:column;align-items:flex-start;gap:10px}
.xf-ctas a{display:inline-block;border-radius:40px;padding:9px 18px 8px;font-weight:700;font-size:18px;line-height:1.4;border:1.5px solid #009d00}
.xf-ctas .demo{background:#009d00;color:#fff}.xf-ctas .demo:hover{background:#fff;color:#009d00}
.xf-ctas .contact{background:#fff;color:#009d00}.xf-ctas .contact:hover{background:#009d00;color:#fff}
.xf-col h4{font-size:18px;font-weight:700;text-transform:uppercase;margin:0 0 22px;padding-top:14px;position:relative}
.xf-col h4::before{content:'';position:absolute;top:0;left:0;width:100%;height:3px}
.xf-col.g h4{color:#4cdb00}.xf-col.g h4::before{background:linear-gradient(90deg,#009d00,transparent)}
.xf-col.b h4{color:#27b2ff}.xf-col.b h4::before{background:linear-gradient(90deg,#006bff,transparent)}
.xf-col.p h4{color:#b383ff}.xf-col.p h4::before{background:linear-gradient(90deg,#982cff,transparent)}
.xf-col a{display:block;color:#fff;font-weight:500;font-size:16.5px;margin-bottom:13px}.xf-col a:hover{color:#4cdb00}
.xf-dark{background:#000;color:#bdbdbd;padding:48px 0 12px}
.xf-row{display:grid;grid-template-columns:repeat(4,1fr);gap:24px;margin-bottom:40px}
.xf-row h4{font-size:18px;font-weight:700;text-transform:uppercase;color:#bdbdbd;margin:0;padding-top:14px;border-top:3px solid #666;align-self:start}
.xf-row h4.cat{color:#4cdb00;border-top-color:#009d00}
.xf-links{grid-column:2/5;display:grid;grid-template-columns:repeat(3,1fr);gap:0 24px;padding-top:14px}
.xf-links a{color:#bdbdbd;font-weight:500;font-size:16.5px;margin-bottom:13px}.xf-links a:hover{color:#fff}
.xf-legal{background:#000;color:#fff;padding:24px 0 48px;font-size:16.5px;font-weight:500}
.xf-legal .xw{display:flex;justify-content:center;gap:50px;flex-wrap:wrap}.xf-legal a{color:#fff}.xf-legal a:hover{color:#4cdb00}
@media(max-width:920px){.xf-top .xw{grid-template-columns:1fr 1fr;row-gap:44px}.xf-row{grid-template-columns:1fr}.xf-links{grid-column:auto;grid-template-columns:1fr 1fr}}
@media(max-width:560px){.xf-top .xw,.xf-links{grid-template-columns:1fr}.xf-legal .xw{flex-direction:column;align-items:center;gap:14px}}
"""


def footer(root, anchors=("about", "install", "plugins", "terms")):
    """www.exabeam.com's footer, plus a Plug-in Catalog row. `root` is the relative path to the catalog's front door."""
    h = html.escape
    _, logo_footer = logos()
    url = lambda u: u if u.startswith("http") else EXABEAM + u
    link = lambda t, u: f'<a href="{h(url(u))}">{h(t)}</a>'
    cols = "".join(f'<div class="xf-col {c}"><h4>{h(title)}</h4>{"".join(link(t, u) for t, u in links)}</div>'
                   for title, c, links in FOOTER_COLUMNS)
    catalog = [(a.title(), f"{root}#{a}") for a in anchors] + [
        ("vendor.lock.json", f"{REPO_URL}/blob/main/vendor.lock.json"), ("Maintainers", f"{REPO_URL}/blob/main/MAINTAINERS.md"),
        ("GitHub repository", REPO_URL), ("Enterprise Agreement", TERMS_URL)]
    return f"""<footer class="xf">
  <div class="xf-top"><div class="xw">
    <div class="xf-brand">
      <a href="{EXABEAM}/" aria-label="Exabeam"><img src="{logo_footer}" alt="Exabeam"></a>
      <p>385 Interlocken Crescent<br>Suite 1050<br>Broomfield, CO 80021</p>
      <p><a href="tel:18443922326">1.844.EXABEAM</a><br><a href="mailto:info@exabeam.com">info@exabeam.com</a></p>
      <div class="xf-social">Follow Us:<div class="icons">{SOCIAL}</div></div>
      <div class="xf-ctas"><a class="demo" href="{EXABEAM}/contact/get-a-demo/">Get a Demo</a><a class="contact" href="{EXABEAM}/contact/contact-us/">Contact Us</a></div>
    </div>
    {cols}
  </div></div>
  <div class="xf-dark"><div class="xw">
    <div class="xf-row"><h4 class="cat">Plug-in Catalog</h4><div class="xf-links">{"".join(f'<a href="{h(u)}">{h(t)}</a>' for t, u in catalog)}</div></div>
    <div class="xf-row"><h4>Security Explainers</h4><div class="xf-links">{"".join(link(t, u) for t, u in FOOTER_EXPLAINERS)}</div></div>
  </div></div>
  <div class="xf-legal"><div class="xw"><span>© 2026 Exabeam</span>{"".join(link(t, u) for t, u in FOOTER_LEGAL)}</div></div>
</footer>"""


COPY_JS = """document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',()=>{
  const pre=b.closest('.codeblock').querySelector('pre').cloneNode(true);pre.querySelectorAll('.c').forEach(c=>c.remove());
  navigator.clipboard.writeText(pre.innerText.replace(/[ \\t]+$/gm,'').trim()).then(()=>{const t=b.textContent;b.textContent='Copied!';setTimeout(()=>b.textContent=t,1500)});
}));"""

LIGHTBOX_JS = """const lb=document.getElementById('lightbox');
document.querySelectorAll('.shot button').forEach(b=>b.addEventListener('click',()=>{
  const i=b.querySelector('img');lb.querySelector('img').src=i.src;lb.querySelector('img').alt=i.alt;lb.showModal();
}));
lb.addEventListener('click',e=>{if(e.target===lb)lb.close()});"""


def codeblock(label, body):
    return f'<div class="codeblock"><div class="codehead"><span>{label}</span><button class="copy" type="button">Copy</button></div><pre>{body}</pre></div>'


def install_blocks(e, mp):
    h = html.escape
    claude = f'claude plugin marketplace add Exabeam-Labs/plugins   <span class="c"># once</span>\nclaude plugin install {h(e["name"])}@{mp}'
    codex = f'codex plugin marketplace add Exabeam-Labs/plugins    <span class="c"># once</span>\ncodex plugin add {h(e["name"])}@{mp}'
    blocks = {"claude": codeblock("Claude Code", claude), "codex": codeblock("OpenAI Codex", codex)}
    return "".join(blocks[x] for x in (e["hosts"] or ["claude", "codex"]) if x in blocks)


def host_pills(e):
    return "".join(f'<span class="pill">{html.escape(HOST_LABELS.get(x, x))}</span>' for x in e["hosts"])


def category(e):
    return e["category"].title() if e["category"] else "Plugin"


STATUS_LABELS = {"pre-release": "Pre-release", "released": "Released"}


def release_status(e):
    """ "pre-release" or "released": site/<name>.json's "status" when set, else read from the README's Status
    section (a status that says pre-release is pre-release; any other stated status is released); "" if unknown."""
    if e.get("site_status"):
        if e["site_status"] not in STATUS_LABELS:
            raise SystemExit(f"site/{e['name']}.json: status must be one of {sorted(STATUS_LABELS)}")
        return e["site_status"]
    said = " ".join(e["readme"].get("status", []))
    return ("pre-release" if re.search(r"pre-?release", said, re.I) else "released") if said else ""


def status_pill(status):
    return f'<span class="pill st-{status}">{STATUS_LABELS[status]}</span>' if status in STATUS_LABELS else ""


CARD_DESC_MAX = 400      # 4x the one-line shortDescription the cards first carried (99 characters)


def clamp_description(text, limit=CARD_DESC_MAX):
    """Fit a card description to `limit` characters: whole sentences when the last one that fits ends past half
    the limit, otherwise cut at a word with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit // 2:
        return cut[:end + 1]
    return cut[:cut.rfind(" ")].rstrip(" ,;:—-") + "…"


def card_description(e):
    """site/<name>.json "card_description" when set, else the catalog description; either way at most CARD_DESC_MAX."""
    return clamp_description(e.get("card_description") or e["description"] or e["short"])


def card_data(e):
    """What a plugin's summary card shows. Detail pages carry this as JSON so a listing can be built from them."""
    return {"name": e["name"], "display": e["display"], "category": category(e),
            "short": card_description(e),
            "hosts": [HOST_LABELS.get(x, x) for x in e["hosts"]], "skills": len(e["skills"]),
            "skill_names": [x["name"] for x in e["skills"]], "version": e["version"],
            "status": release_status(e)}


def card_html(c, href):
    """The plugin summary card: monogram, category, name, one-liner, hosts, skill count and version."""
    h = html.escape
    names = c.get("skill_names") or []
    n = c.get("skills") or len(names)
    meta = [f'<span><b>{n}</b> skill{"s" if n != 1 else ""}</span>' if n else "",
            f'<span>Version <b>{h(c["version"])}</b></span>' if c.get("version") else ""]
    hosts = "".join(f'<span class="pill">{h(x)}</span>' for x in c.get("hosts", []))
    sks = "".join(f'<span class="pill sk">{h(x)}</span>' for x in names)
    skill_pills = f'<div class="tags skills" aria-label="Skills">{sks}</div>' if names else ""
    desc = clamp_description(c.get("short", ""))
    return f"""
    <article class="card pcard" id="{h(c['name'])}">
      <div class="head"><div class="icon" aria-hidden="true">{h(c['name'][:3])}</div><div class="tags"><span class="pill b">{h(c.get('category') or 'Plugin')}</span>{status_pill(c.get('status'))}</div></div>
      <h3><a href="{h(href)}">{h(c['display'])}</a></h3>
      <p>{h(desc)}</p>
      <div class="tags">{hosts}</div>
      <div class="meta">{''.join(meta)}</div>
      {skill_pills}
      <span class="more">View details →</span>
    </article>"""


def paced(text, target=320):
    """Break a one-paragraph description into readable paragraphs at sentence ends, about `target` chars each."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip()):
        cur = f"{cur} {sent}".strip()
        if len(cur) >= target:
            out.append(cur); cur = ""
    return out + ([cur] if cur else [])


def detail_parts(catalog, e, root):
    """A plugin's detail content, independent of page design: the sections, the sub-menu entries for the sections
    present, the Details facts and the Resources links. `root` is the relative path back to the repository root."""
    h = html.escape
    mp = h(catalog["name"])
    doc = e["readme"]
    sections = []

    overview = "".join(f"<p>{h(p)}</p>" for p in paced(e["description"]))
    shots = "".join(
        f'<figure class="shot"><button type="button" aria-label="Enlarge image"><img src="{root}{h(m["src"])}" alt="{h(m.get("alt", ""))}" loading="lazy"></button>'
        + (f'<figcaption>{h(m["caption"])} · Select to enlarge.</figcaption>' if m.get("caption") else "") + "</figure>"
        for m in e["media"])
    sections.append(f'<section id="overview"><div class="kicker">Overview</div><h2>What it is</h2><div class="overview">{overview}</div>{shots}</section>')

    if doc.get("highlights"):
        items = "".join(f"<li>{x}</li>" for x in doc["highlights"])
        sections.append(f'<section id="capabilities"><div class="kicker">Capabilities</div><h2>What it does</h2><ul class="highlights">{items}</ul></section>')

    if e["skills"]:
        rows = ""
        for s in e["skills"]:
            role = doc.get("roles", {}).get(s["name"], {})
            ask = " · ".join(f"“{h(a)}”" for a in s["ask"])
            rows += (f'<div class="card skillrow"><div class="name"><code>{h(s["name"])}</code>'
                     + (f'<div class="who">{role["who"]}</div>' if role.get("who") else "") + "</div><div>"
                     + f'<p>{role.get("what") or h(s["summary"])}</p>'
                     + (f'<p class="ask">Ask: {ask}</p>' if ask else "") + "</div></div>")
        n = len(e["skills"])
        sections.append(f'<section id="skills"><div class="kicker">Skills</div><h2>{n} skill{"s" if n != 1 else ""}</h2><div class="skillrows">{rows}</div></section>')

    steps = "".join(f"<li>{x}</li>" for x in doc.get("steps", []))
    sections.append(f'<section id="install"><div class="kicker">Install</div><h2>Get started</h2>'
                    + (f'<ol class="steps">{steps}</ol>' if steps else "")
                    + f'<div class="grid2">{install_blocks(e, mp)}</div></section>')

    if doc.get("docs"):
        links = "".join(f'<a class="card" href="{h(d["href"])}"><b>{h(d["title"])}</b><span>{d["what"]}</span></a>' for d in doc["docs"])
        sections.append(f'<section id="docs"><div class="kicker">Documentation</div><h2>Guides and reference</h2><div class="doclist">{links}</div></section>')

    if doc.get("status"):
        sections.append(f'<section id="status"><div class="kicker">Status</div><h2>Release status</h2><div class="status">{"".join(f"<p>{p}</p>" for p in doc["status"])}</div></section>')

    facts = [("Version", h(e["version"])), ("Category", h(category(e))),
             ("Hosts", ", ".join(h(HOST_LABELS.get(x, x)) for x in e["hosts"])),
             ("Plugin key", f'<code>{h(e["name"])}@{mp}</code>')]
    if e["sha"]:
        sha = f'<code>{h(e["sha"][:7])}</code>'
        facts.append(("Build", f'<a href="{REPO_URL}/blob/main/vendor.lock.json">{sha}</a>'))
    if e["blessed"]: facts.append(("Vendored", h(e["blessed"])))
    if e["blessed_by"]: facts.append(("Blessed by", h(e["blessed_by"])))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts if v)
    links = []
    if e["setup"]: links.append(f'<a href="{REPO_URL}/blob/main/{h(e["setup"])}">Setup guide →</a>')
    if e["docs"]: links.append(f'<a href="{REPO_URL}/blob/main/{h(e["docs"])}">README →</a>')
    links.append(f'<a href="{REPO_URL}/blob/main/{h(e["name"])}/LICENSE">License →</a>')

    labels = {"overview": "Overview", "capabilities": "Capabilities", "skills": "Skills", "install": "Install",
              "docs": "Documentation", "status": "Status"}
    present = [(i, t) for i, t in labels.items() if any(f'<section id="{i}">' in x for x in sections)]
    return sections, present, dl, links


# ---------- plugin docs: the payload's markdown guide, rendered as site pages ----------

def gh_slug(text):
    """GitHub's heading anchor: lowercase, punctuation dropped, each space a hyphen (so in-doc #links keep working)."""
    t = re.sub(r"<[^>]+>|[`*_]", "", text).strip().lower()
    return re.sub(r" ", "-", re.sub(r"[^\w\- ]", "", t))


def doc_set(e):
    """A plugin's doc pages, in reading order: docs/index.md as the overview, then the README's Documentation table.
    Each: slug, title, what (the table's description), src (path inside the plugin)."""
    name = e["name"]
    pdir = ROOT / name
    out, seen = [], set()
    if (pdir / "docs" / "index.md").exists():
        out.append({"slug": "", "title": "Overview", "what": "What the plugin is, its skills, and how it works.", "src": "docs/index.md"})
        seen.add("docs/index.md")
    for d in e["readme"].get("docs", []):
        prefix = f"{REPO_URL}/blob/main/{name}/"
        if not d["href"].startswith(prefix):
            continue
        src = d["href"][len(prefix):].split("#")[0]
        if src in seen or not (pdir / src).exists():
            continue
        seen.add(src)
        slug = re.sub(r"-+", "-", gh_slug(re.sub(r"&.*?;", "", d["title"]))).strip("-") or Path(src).stem
        out.append({"slug": slug, "title": d["title"], "what": re.sub(r"<[^>]+>", "", d["what"]), "src": src})
    if out and (pdir / "LICENSE").exists():
        out.append({"slug": "license", "title": "License", "kind": "license", "src": "LICENSE",
                    "what": "The terms this plugin is distributed under, and the open-source software it includes."})
    return out


def legal_text_html(txt, resolve, skip_title=False):
    """A plain-text legal file (LICENSE, NOTICE) laid out as page text, wording untouched: blank-line paragraphs,
    an indented address on its own line as a link, web addresses linked, sibling license files linked."""
    paras = [p for p in re.split(r"\n\s*\n", txt.strip()) if p.strip()]
    if skip_title and paras:
        paras = paras[1:]
    out = []
    for para in paras:
        lines = para.splitlines()
        short = len(lines) > 1 and all(len(ln.strip()) < 60 for ln in lines)          # a header block, not wrapped prose
        body = ("<br>" if short else " ").join(html.escape(ln.strip(), quote=False) for ln in lines)
        def link(m):
            url = m.group(0).rstrip(".,;:")
            return f'<a href="{url}">{url}</a>{m.group(0)[len(url):]}'
        body = re.sub(r"https?://[^\s<>()]+", link, body)
        body = re.sub(r"\b(LICENSE-APACHE|NOTICE)\b(?![^<]*</a>)", lambda m: f'<a href="{html.escape(resolve(m.group(1)))}"><code>{m.group(1)}</code></a>', body)
        cls = ' class="lic-url"' if all(ln.startswith("    ") for ln in lines) else (' class="lic-copy"' if body.startswith("Copyright") else "")
        out.append(f"<p{cls}>{body}</p>")
    return "\n".join(out)


def md_to_html(md, resolve, project="", name_as="the plugin"):
    """The markdown the payload docs use, as HTML: headings (with GitHub anchors), paragraphs, one level of nested
    lists, tables, blockquotes, fenced code (mermaid kept as a diagram), rules and raw HTML blocks (a <picture> keeps
    its light image). `resolve(href)` maps a link or image path. Returns (html, [(level, id, text)])."""
    md = in_catalog_voice(re.sub(r"<!--.*?-->", "", md, flags=re.S), project, name_as)
    heads = []

    def inline(t):
        codes = []
        def keep(m):
            codes.append(f"<code>{html.escape(m.group(1))}</code>"); return f"\x00{len(codes) - 1}\x00"
        t = re.sub(r"`([^`]+)`", keep, t)
        t = html.escape(t, quote=False).replace("&lt;br/&gt;", "<br>").replace("&lt;br&gt;", "<br>")
        t = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: f'<img src="{html.escape(resolve(m.group(2)))}" alt="{m.group(1)}">', t)
        t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(resolve(m.group(2)))}">{m.group(1)}</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
        t = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", t)
        return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], t)

    def cells(row):
        return [c.strip() for c in row.strip().strip("|").split("|")]

    list_re = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
    block_start = re.compile(r"^(#{1,6}\s|```|>|\s*([-*+]|\d+\.)\s|\||<(p|picture|div|details|table|img)\b|(-{3,}|\*{3,})\s*$)")

    def render(lines):
        out, i = [], 0
        while i < len(lines):
            ln = lines[i]
            if not ln.strip():
                i += 1; continue
            m = re.match(r"^```\s*([\w+-]*)", ln)
            if m:
                lang, j = m.group(1), i + 1
                while j < len(lines) and not lines[j].startswith("```"):
                    j += 1
                code = "\n".join(lines[i + 1:j])
                if lang == "mermaid":
                    out.append(f'<pre class="mermaid">{html.escape(code)}</pre>')
                else:
                    label = {"bash": "Shell", "sh": "Shell", "jsonc": "JSON", "json": "JSON", "": "Code"}.get(lang, lang.title())
                    out.append(codeblock(label, html.escape(code)))
                i = j + 1; continue
            m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
            if m:
                lvl, txt = len(m.group(1)), m.group(2)
                hid = gh_slug(txt)
                heads.append((lvl, hid, re.sub(r"<[^>]+>", "", inline(txt))))
                out.append(f'<h{lvl} id="{hid}">{inline(txt)}</h{lvl}>')
                i += 1; continue
            if re.match(r"^(-{3,}|\*{3,})\s*$", ln):
                out.append("<hr>"); i += 1; continue
            if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
                head, j, rows = cells(ln), i + 2, []
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    rows.append(cells(lines[j])); j += 1
                th = "".join(f"<th>{inline(c)}</th>" for c in head)
                tb = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                out.append(f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>')
                i = j; continue
            if ln.startswith(">"):
                j = i
                while j < len(lines) and lines[j].startswith(">"):
                    j += 1
                out.append("<blockquote>" + render([re.sub(r"^> ?", "", x) for x in lines[i:j]]) + "</blockquote>")
                i = j; continue
            if list_re.match(ln):
                items, j = [], i
                while j < len(lines):
                    lm = list_re.match(lines[j])
                    if lm:
                        items.append([len(lm.group(1)), lm.group(2)[-1] == ".", lm.group(3)])
                    elif lines[j].strip() and lines[j].startswith(" ") and items:
                        items[-1][2] += " " + lines[j].strip()
                    elif not lines[j].strip() and j + 1 < len(lines) and (list_re.match(lines[j + 1]) or lines[j + 1].startswith("  ")):
                        pass
                    else:
                        break
                    j += 1
                out.append(render_list(items))
                i = j; continue
            if re.match(r"^\s*<(p|picture|div|details|table|img)\b", ln):
                j = i
                while j < len(lines) and lines[j].strip():
                    j += 1
                raw = "\n".join(lines[i:j])
                raw = re.sub(r"<source[^>]*>", "", raw)
                raw = re.sub(r'(src)="([^"]+)"', lambda m: f'{m.group(1)}="{html.escape(resolve(m.group(2)))}"', raw)
                raw = re.sub(r'\s(width|align)="[^"]*"', "", raw)
                out.append(f'<div class="figure">{raw}</div>')
                i = j; continue
            j, para = i, []
            while j < len(lines) and lines[j].strip() and not (j > i and block_start.match(lines[j])):
                para.append(lines[j].strip()); j += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
            i = j
        return "\n".join(out)

    def render_list(items):
        base = items[0][0]
        tag = "ol" if items[0][1] else "ul"
        out, k = [f"<{tag}>"], 0
        while k < len(items):
            ind, _, txt = items[k]
            sub, k2 = [], k + 1
            while k2 < len(items) and items[k2][0] > base:
                sub.append(items[k2]); k2 += 1
            out.append(f"<li>{inline(txt)}{render_list(sub) if sub else ''}</li>")
            k = k2
        return "".join(out) + f"</{tag}>"

    return render(md.splitlines()), heads



def no_em_dashes(text):
    """House style for the site's copy: no em dashes. Applied to the rendered page, since most of the copy comes
    from the vendored payload, which must stay byte-identical to upstream."""
    return re.sub(r"\s*—\s*", ", ", text)


SITE_CSS = """
:root{--green:#009d00;--green-l:#4cdb00;--blue:#006bff;--blue-l:#27b2ff;--purple:#982cff;--teal:#00968f;--yellow:#ffc000;
--xs-max:1128px;--xs-pad:24px;--ink:#000;--body:#1f1f1f;--mute:#6f6f6f;--gray:#f7f7f7;--line:#dedede;--dark:#313131;--code:#1e1e1e;--maxw:1128px;}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:#fff;color:var(--body);font-family:'Lausanne','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
font-size:16px;line-height:1.7;-webkit-font-smoothing:antialiased}
a{color:var(--green)}
code,pre{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 24px}
.wide{max-width:1296px;margin:0 auto;padding:0 24px}
section{scroll-margin-top:96px}
.btn{display:inline-flex;align-items:center;justify-content:center;border-radius:40px;padding:10px 19px 9px;font-weight:700;font-size:18px;line-height:1.4;
text-decoration:none;border:1.5px solid transparent;white-space:nowrap;transition:background .15s,color .15s,border-color .15s}
.btn-green{background:var(--green);border-color:var(--green);color:#fff}.btn-green:hover{background:#fff;color:var(--green)}
.btn-line{background:#fff;border-color:var(--green);color:var(--green)}.btn-line:hover{background:var(--green);color:#fff}
.btn-white{background:#fff;border-color:#fff;color:var(--green)}.btn-white:hover{background:transparent;color:#fff}
/* hero, after /industries/financial-services/ */
.hero{position:relative;background:var(--gray);overflow:hidden}
.hero .wrap{position:relative;z-index:2;padding-top:78px;padding-bottom:104px}
.crumbs{font-size:16px;color:var(--mute);margin-bottom:26px}.crumbs a{color:var(--mute);text-decoration:none}.crumbs a:hover{color:var(--green)}
.crumbs span{margin:0 12px}
.hero h1{font-size:56px;line-height:1.12;font-weight:800;letter-spacing:-.01em;color:var(--green);margin:0 0 34px;max-width:620px;text-wrap:balance}
.hero p{font-size:17px;line-height:1.75;color:#000;max-width:640px;margin:0 0 26px;font-weight:500}
.ctas{display:flex;gap:26px;flex-wrap:wrap}
.visual{position:absolute;z-index:1;top:0;right:0;bottom:0;width:58%;pointer-events:none}
.visual::before{content:'';position:absolute;inset:0;z-index:2;background:linear-gradient(90deg,var(--gray) 0%,rgba(247,247,247,.85) 22%,rgba(247,247,247,0) 55%)}
.rings{position:absolute;right:-120px;top:50%;width:760px;height:760px;transform:translateY(-50%)}
.term{position:absolute;z-index:3;right:5%;top:50%;transform:translateY(-50%);width:500px;border-radius:10px;background:var(--code);
box-shadow:0 30px 60px rgba(0,0,0,.18);overflow:hidden}
.term .bar{display:flex;gap:7px;padding:12px 14px;background:#2a2a2a}.term .bar i{width:11px;height:11px;border-radius:50%;background:#555}
.term pre{margin:0;padding:18px 20px 22px;color:#d6e0f5;font-size:13px;line-height:1.85;white-space:pre;overflow:hidden}
.term .p{color:#4cdb00}.term .c{color:#7884a0}.term .o{color:#27b2ff}
/* two-column "benefits" layout */
.benefits{padding:96px 0}
.benefits .wrap{display:grid;grid-template-columns:1fr 1fr;gap:48px}
h2{font-size:40px;line-height:1.14;font-weight:800;letter-spacing:-.01em;color:#000;margin:0 0 24px}
h2 .g{color:var(--green)}
h2 .grad{background:linear-gradient(90deg,#009d00,#006bff 60%,#982cff);-webkit-background-clip:text;background-clip:text;color:transparent}
.benefits .intro{font-size:17px;color:var(--body);margin:0}
.points h3{font-size:24px;line-height:1.3;font-weight:800;color:var(--green);margin:0 0 12px}
.points p{margin:0 0 30px;font-size:16px}.points p:last-child{margin:0}
/* gray centered sections */
.band-gray{background:var(--gray);padding:96px 0}
.center{text-align:center;max-width:900px;margin:0 auto 56px}.center p{margin:0;font-size:17px}.center p+p{margin-top:14px}
.outlined{display:grid;grid-template-columns:1fr 1fr;gap:32px}
.outlined>*,.benefits .wrap>*{min-width:0}
.ocard{border:2px solid var(--green);border-radius:16px;background:#fff;padding:32px 30px}
.ocard.b{border-color:var(--blue)}
.ocard h3{font-size:22px;font-weight:800;color:var(--green);margin:0 0 16px}.ocard.b h3{color:var(--blue)}
.ocard pre{margin:0;background:var(--code);color:#d6e0f5;border-radius:8px;padding:16px 18px;font-size:13px;line-height:1.7;overflow-x:auto}
.copy{float:right;margin-top:4px;font:700 13px/1 inherit;font-family:inherit;color:var(--mute);background:none;border:none;cursor:pointer}
.copy:hover{color:#000}
.plugins{padding:96px 0}
/* green call-to-action band */
.cta{background:var(--green);color:#fff}
.cta .wrap{display:flex;align-items:center;justify-content:center;gap:44px;flex-wrap:wrap;padding-top:34px;padding-bottom:34px}
.cta strong{font-size:36px;line-height:1.2;font-weight:800}
/* terms */
.terms p{margin:0 0 18px;font-size:16px}.terms p:last-child{margin:0}
.terms code{font-size:.88em;background:#f1f1f1;padding:1px 6px;border-radius:4px}
@media(max-width:1280px){.term{display:none}}
@media(max-width:1100px){.visual{width:50%;opacity:.5}.term{display:none}}
@media(max-width:900px){.benefits .wrap,.outlined{grid-template-columns:1fr}
.hero h1{font-size:42px}h2{font-size:32px}.cta strong{font-size:28px}.visual{display:none}}
@media(max-width:640px){:root{--xs-pad:16px}.wrap,.wide{padding:0 16px}
.btn{font-size:16px;padding:9px 16px 8px}.hero .wrap{padding-top:44px;padding-bottom:60px}.hero h1{font-size:36px}
.benefits,.band-gray,.plugins{padding:64px 0}}
"""

RINGS = """<svg class="rings" viewBox="0 0 760 760" aria-hidden="true"><defs>
<linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4cdb00"/><stop offset=".5" stop-color="#27b2ff"/><stop offset="1" stop-color="#b383ff"/></linearGradient></defs>
<g fill="none" stroke="url(#rg)">""" + "".join(
    f'<circle cx="380" cy="380" r="{r}" stroke-width="{w}" opacity="{o}"/>'
    for r, w, o in [(370, 1.5, .35), (318, 2, .5), (266, 2.5, .65), (214, 3, .8)]) + "</g></svg>"

LANDING_JS = """document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',()=>{
  const pre=b.parentElement.querySelector('pre');navigator.clipboard.writeText(pre.innerText.trim()).then(()=>{const t=b.textContent;b.textContent='Copied!';setTimeout(()=>b.textContent=t,1500)});
}));"""


# ---------- plugin cards and detail pages ----------
PLUGINS = ROOT / "plugins"                      # plugins/<name>/index.html, one per card
MARK = "<!-- generated by scripts/build_site.py: edits are overwritten; copy plugins/_template/ to hand-author a page -->"
CARD_RE = re.compile(r'<script type="application/json" id="plugin-card">(.*?)</script>', re.S)

CARD_CSS = """
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 3px rgba(15,25,50,.05),0 8px 24px rgba(15,25,50,.05)}
.pcard{position:relative;display:flex;flex-direction:column;gap:14px;padding:30px;color:var(--body);transition:border-color .15s,transform .15s,box-shadow .15s}
.pcard:hover{border-color:var(--green);transform:translateY(-2px);box-shadow:0 2px 6px rgba(15,25,50,.06),0 14px 32px rgba(15,25,50,.1)}
.pcard .head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.pcard .icon{width:46px;height:46px;border-radius:10px;display:flex;align-items:center;justify-content:center;
background:linear-gradient(135deg,#009d00,#006bff);color:#fff;font-weight:800;font-size:15px;letter-spacing:.02em;text-transform:uppercase}
.pcard h3{font-size:22px;line-height:1.3;margin:0;font-weight:800;color:#000}
.pcard h3 a{color:inherit;text-decoration:none}.pcard h3 a::after{content:'';position:absolute;inset:0;border-radius:12px}
.pcard p{margin:0;color:var(--body);font-size:16px}
.pcard:not(.soon)>p{margin:1lh 0}   /* one extra line of space above and below the description */
@supports not (margin:1lh){.pcard:not(.soon)>p{margin:1.7em 0}}
.pcard .meta{display:flex;gap:18px;font-size:14px;color:var(--mute);margin-top:auto;padding-top:14px;border-top:1px solid var(--line)}
.pcard .meta b{color:#000;font-weight:700}
.pcard .more{align-self:flex-start;display:inline-flex;align-items:center;gap:6px;background:var(--green);color:#fff;
font-size:16px;font-weight:700;line-height:1.4;padding:9px 20px 8px;border:1.5px solid var(--green);border-radius:40px;transition:background .15s,color .15s}
.pcard:hover .more{background:#fff;color:var(--green)}
.pcard.soon{border:2px dashed #cecece;background:var(--gray);box-shadow:none;justify-content:center;text-align:center}
.pcard.soon:hover{transform:none;border-color:#cecece}.pcard.soon a{position:relative;color:var(--green)}
.tags{display:flex;gap:8px;flex-wrap:wrap}
.hero-tags{margin-top:30px}
.pill{display:inline-block;font-size:11.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:4px 11px;border-radius:40px;
color:#3a3a3a;background:#fff;border:1px solid #cecece;white-space:nowrap}
.pill.g{color:var(--green);border-color:rgba(0,157,0,.35);background:rgba(0,157,0,.06)}
.pill.b{color:var(--blue);border-color:rgba(0,107,255,.3);background:rgba(0,107,255,.06)}
.pill.sk{font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;font-size:12.5px;font-weight:600;letter-spacing:0;text-transform:none;
color:#000;background:var(--gray);border-color:var(--line);border-radius:6px}
.pill.st-pre-release{color:#8a5a00;background:#fff4d6;border-color:#f0c96a}
.pill.st-released{color:#fff;background:var(--green);border-color:var(--green)}
/* detail page */
.dh .wrap{padding-bottom:84px}.dh h1{max-width:760px}.dh .tags{margin:0 0 30px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:56px;align-items:start;padding-top:56px;padding-bottom:40px}
.layout .content>section{padding:0 0 56px;margin-bottom:56px;border-bottom:1px solid var(--line)}
.layout .content>section:last-child{border-bottom:none;margin-bottom:0}
.layout section{scroll-margin-top:100px}
.kicker{font-size:14px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--green);margin-bottom:10px}
.layout h2{font-size:34px;margin-bottom:22px}
.overview p{margin:0 0 16px;font-size:17px}
.shot{margin:28px 0 0}.shot button{display:block;width:100%;padding:0;border:1px solid var(--line);border-radius:12px;background:var(--gray);cursor:zoom-in;overflow:hidden}
.shot img{display:block;width:100%;height:auto;max-height:560px;object-fit:contain;object-position:top}
.shot figcaption{font-size:14px;color:var(--mute);margin-top:10px}
.highlights{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:28px 32px}
.highlights li{font-size:16px;border-top:3px solid var(--green);padding-top:14px}
.highlights li:nth-child(4n+2){border-color:#00968f}.highlights li:nth-child(4n+3){border-color:var(--blue)}.highlights li:nth-child(4n+4){border-color:var(--purple)}
.highlights li b:first-child{display:block;font-size:20px;font-weight:800;color:#000;margin-bottom:4px}
.skillrows{display:flex;flex-direction:column;gap:16px}
.skillrow{display:grid;grid-template-columns:230px 1fr;gap:24px;padding:26px 28px;border-radius:16px;border:2px solid var(--green);box-shadow:none}
.skillrow:nth-child(2){border-color:var(--blue)}.skillrow:nth-child(3){border-color:var(--purple)}
.skillrow .name code{font-size:15px;font-weight:600;color:#000}
.skillrow .who{font-size:13px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--green);margin-top:6px}
.skillrow:nth-child(2) .who{color:var(--blue)}.skillrow:nth-child(3) .who{color:var(--purple)}
.skillrow p{margin:0 0 8px}.skillrow .ask{font-size:14px;color:var(--mute);margin:0}
.steps{counter-reset:s;list-style:none;padding:0;margin:0 0 28px;display:flex;flex-direction:column;gap:14px}
.steps li{counter-increment:s;position:relative;padding-left:48px;min-height:32px}
.steps li::before{content:counter(s);position:absolute;left:0;top:-2px;width:32px;height:32px;border-radius:50%;background:var(--green);color:#fff;
font-weight:800;font-size:15px;display:flex;align-items:center;justify-content:center}
.steps code,.overview code,.highlights code,.skillrow p code,.status code{font-size:.86em;background:#f1f1f1;padding:1px 6px;border-radius:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}.grid2>*{min-width:0}
.codeblock{background:var(--code);border-radius:10px;overflow:hidden}
.codehead{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid #333;font-size:13px;font-weight:700;color:#bdbdbd}
.codeblock .copy{float:none;margin:0;color:#4cdb00}.codeblock .copy:hover{color:#fff}
.codeblock pre{margin:0;padding:16px 18px;overflow-x:auto;font-size:13px;line-height:1.7;color:#d6e0f5}.codeblock .c{color:#7884a0}
.doclist{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.doclist a{display:block;padding:22px 24px;color:var(--body);text-decoration:none;transition:border-color .15s,transform .15s}
.doclist a:hover{border-color:var(--green);transform:translateY(-2px)}
.doclist a>b{display:block;font-size:18px;font-weight:800;color:var(--green);margin-bottom:6px}.doclist a>b::after{content:" >"}
.doclist span{font-size:15px}
.status{background:var(--gray);border-left:4px solid var(--yellow);padding:20px 24px}.status p{margin:0}
aside.facts{position:sticky;top:100px;display:flex;flex-direction:column;gap:20px}
aside .card{padding:26px}
aside h4{font-size:14px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--green);margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid var(--line)}
aside dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:10px 16px;font-size:15px}
aside dt{color:var(--mute)}aside dd{margin:0;color:#000;font-weight:700;overflow-wrap:anywhere}
aside dd code{font-weight:500;font-size:.88em;background:#f1f1f1;padding:1px 6px;border-radius:4px}
aside .links a{display:block;font-weight:700;padding:5px 0;text-decoration:none}
dialog.lightbox{border:none;padding:0;background:transparent;max-width:96vw;max-height:94vh}
dialog.lightbox::backdrop{background:rgba(20,20,24,.82)}
dialog.lightbox img{display:block;max-width:96vw;max-height:88vh;height:auto;border-radius:8px;background:#fff}
dialog.lightbox form{text-align:right;margin-bottom:8px}
dialog.lightbox button{background:#fff;border:none;border-radius:40px;padding:7px 16px;font:700 13px/1 inherit;font-family:inherit;cursor:pointer}
.draft{background:#fff7d6;border:1px solid #f0d27a;padding:14px 18px;border-radius:8px;margin:0 0 28px;font-size:15px}
@media(max-width:1000px){.layout{grid-template-columns:1fr}aside.facts{position:static}}
@media(max-width:900px){.highlights,.grid2,.doclist{grid-template-columns:1fr}.skillrow{grid-template-columns:1fr;gap:10px}.layout h2{font-size:28px}}
@media(max-width:640px){.cards{grid-template-columns:1fr}.pcard{padding:24px}.skillrow{padding:22px}}
"""

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="icon" type="image/x-icon" href="https://www.exabeam.com/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
"""


def head(title, desc):
    return HEAD.format(title=h(title), desc=h(desc), css=SITE_CSS + CARD_CSS + DOCS_CSS + HEADER_CSS + FOOTER_CSS)


def terminal(lines):
    return f'<div class="visual">{RINGS}<div class="term"><div class="bar"><i></i><i></i><i></i></div><pre>{chr(10).join(lines)}</pre></div></div>'


def plugin_terminal(e, mp):
    lines = ['<span class="p">$</span> claude plugin marketplace add Exabeam-Labs/plugins',
             f'<span class="p">$</span> claude plugin install {h(e["name"])}@{mp}']
    if e["skills"]:
        lines.append(f'<span class="c"># skills: {", ".join(h(s["name"]) for s in e["skills"])}</span>')
        if e["skills"][0]["ask"]:
            lines.append(f'<span class="o">&gt;</span> {h(e["skills"][0]["ask"][0])} &lt;id&gt;')
    return terminal(lines)


def card_json(data):
    body = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")   # keep the JSON inside its <script>
    return f'<script type="application/json" id="plugin-card">{body}</script>'


def detail_page(catalog, e):
    """A catalog plugin's detail page in this design, carrying its own card data."""
    mp = h(catalog["name"])
    sections, present, dl, links = detail_parts(catalog, e, "../../")
    tags = "".join(f'<span class="pill">{h(x)}</span>' for x in card_data(e)["hosts"]) + f'<span class="pill b">{h(category(e))}</span>' \
        + status_pill(release_status(e)) + (f'<span class="pill g">v{h(e["version"])}</span>' if e["version"] else "")
    setup = f'<a class="btn btn-line" href="{REPO_URL}/blob/main/{h(e["setup"])}">Setup Guide</a>' if e["setup"] else ""
    data = dict(card_data(e), order=0)
    short = e["short"] or first_sentence(e["description"])
    if doc_set(e):
        links = [f'<a href="docs/">Documentation →</a>'] + links
    return link_docs_locally(MARK + "\n" + head(f"{e['display']} · Exabeam Plug-in Catalog", f"{e['display']}: {short}") + f"""{header()}
<main>
<section class="hero dh">
  {plugin_terminal(e, mp)}
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="https://www.exabeam.com/">Home</a><span>&gt;</span><a href="../../#plugins">Plugins</a><span>&gt;</span>{h(e['display'])}</nav>
    <h1>{h(e['display'])}</h1>
    <p>{h(short)}</p>
    <div class="tags">{tags}</div>
    <div class="ctas"><a class="btn btn-green" href="#install">Install</a>{setup}</div>
  </div>
</section>
{subnav(present)}
<div class="wrap layout">
  <div class="content">{''.join(sections)}</div>
  <aside class="facts">
    <div class="card"><h4>Details</h4><dl>{dl}</dl></div>
    <div class="card links"><h4>Resources</h4>{''.join(links)}</div>
  </aside>
</div>
</main>
<dialog class="lightbox" id="lightbox"><form method="dialog"><button>Close ✕</button></form><img src="" alt=""></dialog>
{footer('../../')}
{card_json(data)}
<script>{COPY_JS}{HEADER_JS}{LIGHTBOX_JS if e['media'] else ''}</script>
</body>
</html>
""", e)


def template_page():
    """A starter for a hand-authored plugin page: copy plugins/_template/ to plugins/<name>/, fill in the card JSON
    (remove "draft") and the sections. The listing picks it up on the next build."""
    data = {"draft": True, "name": "new", "display": "New plugin name", "category": "Security",
            "short": "One sentence on what the plugin does.", "hosts": ["Claude Code", "OpenAI Codex"],
            "skills": 0, "skill_names": ["first-skill"], "version": "0.1.0", "status": "pre-release", "order": 100}
    sections = """
  <section id="overview"><div class="kicker">Overview</div><h2>What it is</h2><div class="overview"><p>Describe the plugin.</p></div></section>
  <section id="install"><div class="kicker">Install</div><h2>Get started</h2><div class="grid2">""" + codeblock("Claude Code", "claude plugin marketplace add Exabeam-Labs/plugins\nclaude plugin install new@exabeam") + codeblock("OpenAI Codex", "codex plugin marketplace add Exabeam-Labs/plugins\ncodex plugin add new@exabeam") + """</div></section>"""
    return (MARK + "\n" + head("New plugin · Exabeam Plug-in Catalog", "Template for a hand-authored plugin page") + f"""{header()}
<main>
<section class="hero dh">
  {terminal(['<span class="p">$</span> claude plugin install new@exabeam'])}
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="https://www.exabeam.com/">Home</a><span>&gt;</span><a href="../../#plugins">Plugins</a><span>&gt;</span>New plugin name</nav>
    <h1>New plugin name</h1>
    <p>One sentence on what the plugin does.</p>
    <div class="ctas"><a class="btn btn-green" href="#install">Install</a></div>
  </div>
</section>
{subnav([("overview", "Overview"), ("install", "Install")])}
<div class="wrap layout">
  <div class="content"><p class="draft">Template: copy <code>plugins/_template/</code> to <code>plugins/&lt;name&gt;/</code>, edit the page and the <code>plugin-card</code> JSON at the bottom (remove <code>"draft"</code>), then rebuild. This page is never listed.</p>{sections}</div>
  <aside class="facts"><div class="card"><h4>Details</h4><dl><dt>Version</dt><dd>0.1.0</dd></dl></div></aside>
</div>
</main>
{footer('../../')}
{card_json(data)}
<script>{COPY_JS}{HEADER_JS}</script>
</body>
</html>
""")

# ---------- docs: each plugin's guide as pages in this design, with a left-column index ----------
DOCS_CSS = """
.dochero .wrap{padding-top:56px;padding-bottom:56px}.dochero h1{font-size:46px;max-width:none;margin-bottom:16px}
.dochero p{margin:0;max-width:760px}
.docs{display:grid;grid-template-columns:270px minmax(0,1fr);gap:56px;align-items:start;padding-top:48px;padding-bottom:72px}
.docnav{position:sticky;top:24px;max-height:calc(100vh - 48px);overflow:auto;font-size:15px;line-height:1.45;padding-right:8px}
.docnav .dn-title{font-size:14px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--green);padding-bottom:12px;margin-bottom:10px;border-bottom:1px solid var(--line)}
.docnav ol{list-style:none;margin:0;padding:0}
.docnav>ol>li>a{display:block;padding:8px 12px;border-left:3px solid transparent;color:#000;font-weight:700;text-decoration:none}
.docnav>ol>li>a:hover{color:var(--green)}
.docnav>ol>li.on>a{border-left-color:var(--green);color:var(--green);background:var(--gray)}
.docnav .toc{list-style:none;margin:4px 0 10px;padding:0 0 0 15px}
.docnav .toc a{display:block;padding:4px 12px;color:var(--mute);font-size:14px;text-decoration:none;border-left:1px solid var(--line)}
.docnav .toc a:hover,.docnav .toc a.on{color:#000;border-left-color:var(--green)}
.docnav .back{display:block;margin-top:18px;padding-top:14px;border-top:1px solid var(--line);font-weight:700;text-decoration:none}
.doc{font-size:16.5px;min-width:0}
.doc>h1:first-child{display:none}
.doc h2{font-size:30px;line-height:1.2;margin:48px 0 16px;padding-top:8px;scroll-margin-top:24px}
.doc h3{font-size:22px;font-weight:800;color:#000;margin:32px 0 10px;scroll-margin-top:24px}
.doc h4{font-size:18px;font-weight:800;margin:24px 0 8px}
.doc>h2:first-of-type{margin-top:0}
.doc p,.doc li{line-height:1.75}.doc p{margin:0 0 16px}
.doc ul,.doc ol{margin:0 0 18px;padding-left:24px}.doc li{margin-bottom:6px}.doc li>ul{margin:6px 0 0}
.doc a{color:var(--green)}
.doc code{font-size:.86em;background:#f1f1f1;padding:1px 6px;border-radius:4px}
.doc .codeblock{margin:0 0 20px}.doc .codeblock code{background:none;padding:0}
.doc blockquote{margin:0 0 20px;padding:14px 20px;border-left:4px solid var(--green);background:var(--gray)}.doc blockquote p:last-child{margin:0}
.doc hr{border:none;border-top:1px solid var(--line);margin:32px 0}
.doc .table-wrap{overflow-x:auto;margin:0 0 22px;border:1px solid var(--line);border-radius:10px}
.doc table{border-collapse:collapse;width:100%;font-size:15px}
.doc th{background:var(--gray);text-align:left;font-weight:700;color:#000;padding:12px 16px;border-bottom:2px solid var(--green)}
.doc td{padding:12px 16px;border-top:1px solid var(--line);vertical-align:top}
.doc .figure{margin:8px 0 28px}.doc .figure p{margin:0}.doc .figure img,.doc img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:10px}
.doc pre.mermaid{background:#fff;border:1px solid var(--line);border-radius:10px;padding:20px;text-align:center;margin:0 0 22px;overflow-x:auto}
.doc .lic-url{margin:-4px 0 16px;padding-left:20px}.doc .lic-copy{color:var(--mute);font-size:15px;margin-top:24px}
.pager{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:56px;padding-top:28px;border-top:1px solid var(--line)}
.pager a{display:block;padding:18px 22px;border:2px solid var(--line);border-radius:12px;text-decoration:none;color:#000}
.pager a:hover{border-color:var(--green)}.pager small{display:block;color:var(--mute);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.pager b{font-size:18px;color:var(--green)}.pager .next{text-align:right;grid-column:2}
@media(max-width:1000px){.docs{grid-template-columns:1fr;gap:28px}.docnav{position:static;max-height:none;border:1px solid var(--line);border-radius:12px;padding:18px}
.docnav .toc{display:none}.dochero h1{font-size:36px}}
@media(max-width:640px){.doc h2{font-size:25px}.pager{grid-template-columns:1fr}.pager .next{grid-column:1}}
"""

TOC_JS = """(()=>{const links=[...document.querySelectorAll('.docnav .toc a')];if(!links.length)return;
const pairs=links.map(a=>[a,document.getElementById(decodeURIComponent(a.hash.slice(1)))]).filter(p=>p[1]);let q=false;
const mark=()=>{q=false;let cur=null;for(const p of pairs){if(p[1].getBoundingClientRect().top<=120)cur=p}links.forEach(a=>a.classList.toggle('on',!!cur&&a===cur[0]))};
addEventListener('scroll',()=>{if(!q){q=true;requestAnimationFrame(mark)}},{passive:true});mark();})();"""

MERMAID = """<script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
mermaid.initialize({startOnLoad:true,theme:'base',themeVariables:{primaryColor:'#f7f7f7',primaryBorderColor:'#009d00',lineColor:'#006bff',fontFamily:'Inter, sans-serif'}});</script>"""


def doc_href(pages, cur, slug, frag=""):
    """Relative link between doc pages: the overview lives at docs/, every other page at docs/<slug>/."""
    up = "../" if cur else ""
    return (up + (f"{slug}/" if slug else "") or "./") + frag


def docs_pages(catalog, e):
    """{path: html} for a plugin's doc pages, rendered from its markdown guide."""
    import os
    pages = doc_set(e)
    if not pages:
        return {}
    name, project = e["name"], (e["upstream"].rsplit("/", 1)[-1] if e["upstream"] else "")
    by_src = {p["src"]: p for p in pages}
    out = {}
    for i, pg in enumerate(pages):
        cur = pg["slug"]
        to_repo = "../" * (4 if cur else 3)            # plugins/<name>/docs[/<slug>]/ -> site / repository root
        src_dir = os.path.dirname(pg["src"])

        def resolve(href, src_dir=src_dir, cur=cur, to_repo=to_repo):
            if re.match(r"[a-z]+:", href) or href.startswith("#"):
                return href
            path, _, frag = href.partition("#")
            target = os.path.normpath(os.path.join(src_dir, path))
            if target in by_src:
                return doc_href(pages, cur, by_src[target]["slug"], f"#{frag}" if frag else "")
            if re.search(r"\.(png|jpe?g|gif|svg|webp)$", target, re.I) and (ROOT / name / target).is_file():
                return f"{to_repo}{name}/{target}"
            return f"{REPO_URL}/blob/main/{name}/{target}" + (f"#{frag}" if frag else "")

        if pg.get("kind") == "license":
            notice = ROOT / name / "NOTICE"
            body = ('<h2 id="terms">Terms</h2>' + legal_text_html((ROOT / name / "LICENSE").read_text(encoding="utf-8"), resolve, skip_title=True)
                    + ('<h2 id="notice">Notice</h2>' + legal_text_html(notice.read_text(encoding="utf-8"), resolve) if notice.exists() else ""))
            heads = [(2, "terms", "Terms")] + ([(2, "notice", "Notice")] if notice.exists() else [])
        else:
            body, heads = md_to_html((ROOT / name / pg["src"]).read_text(encoding="utf-8"), resolve, project, e["display"])
        nav = ""
        for p in pages:
            on = p["slug"] == cur
            toc = "".join(f'<li><a href="#{hid}">{txt}</a></li>' for lvl, hid, txt in heads if lvl == 2) if on else ""
            current = ' aria-current="page"' if on else ""
            nav += (f'<li class="{"on" if on else ""}"><a href="{doc_href(pages, cur, p["slug"])}"{current}>{h(p["title"])}</a>'
                    + (f'<ol class="toc">{toc}</ol>' if toc else "") + "</li>")
        prev_, next_ = (pages[i - 1] if i else None), (pages[i + 1] if i + 1 < len(pages) else None)
        pager = (f'<a class="prev" href="{doc_href(pages, cur, prev_["slug"])}"><small>Previous</small><b>{h(prev_["title"])}</b></a>' if prev_ else "") \
            + (f'<a class="next" href="{doc_href(pages, cur, next_["slug"])}"><small>Next</small><b>{h(next_["title"])}</b></a>' if next_ else "")
        crumbs_doc = (f'<a href="{doc_href(pages, cur, "")}">Documentation</a><span>&gt;</span>{h(pg["title"])}' if cur else "Documentation")
        plugin_page = "../" * (2 if cur else 1)
        page = head(f"{pg['title']} · {e['display']} docs", pg["what"]) + f"""{header()}
<main>
<section class="hero dochero"><div class="wrap">
  <nav class="crumbs" aria-label="Breadcrumb"><a href="https://www.exabeam.com/">Home</a><span>&gt;</span><a href="{to_repo}#plugins">Plugins</a><span>&gt;</span><a href="{plugin_page}">{h(e['display'])}</a><span>&gt;</span>{crumbs_doc}</nav>
  <h1>{h(pg['title'])}</h1>
  <p>{h(pg['what'][:1].upper() + pg['what'][1:])}</p>
</div></section>
<div class="wrap docs">
  <nav class="docnav" aria-label="Documentation"><div class="dn-title">{h(e['display'])} docs</div><ol>{nav}</ol>
    <a class="back" href="{plugin_page}">&lt; Back to the plugin</a></nav>
  <article class="doc">{body}<nav class="pager" aria-label="Pages">{pager}</nav></article>
</div>
</main>
{footer(to_repo)}
<script>{COPY_JS}{HEADER_JS}{TOC_JS}</script>
{MERMAID if 'class="mermaid"' in body else ''}
</body>
</html>
"""
        out[PLUGINS / name / "docs" / (f"{cur}/index.html" if cur else "index.html")] = MARK + "\n" + page
    return out


def link_docs_locally(page, e):
    """Point a detail page's doc links (cards, Setup Guide, Resources) at the rendered doc pages."""
    for p in doc_set(e):
        page = page.replace(f'href="{REPO_URL}/blob/main/{e["name"]}/{p["src"]}"', f'href="docs/{p["slug"] + "/" if p["slug"] else ""}"')
    return page



def discover(generated):
    """Every card to list: one per plugins/<name>/index.html that carries plugin-card JSON and is not a draft, whether
    this build generates it (read from `generated`, so --check is deterministic) or someone hand-authored it."""
    cards = []
    for page in sorted(set(PLUGINS.glob("*/index.html")) | {p for p in generated if p.parent.parent == PLUGINS}):
        if page.parent.name.startswith("_"):
            continue
        m = CARD_RE.search(generated[page] if page in generated else page.read_text(encoding="utf-8"))
        if not m:
            print(f"  skipped {page.relative_to(ROOT)}: no plugin-card JSON"); continue
        try:
            c = json.loads(m.group(1))
        except json.JSONDecodeError as err:
            print(f"  skipped {page.relative_to(ROOT)}: plugin-card JSON is invalid ({err})"); continue
        if c.get("draft") or not c.get("display"):
            continue
        c.setdefault("name", page.parent.name)
        cards.append((c.get("order", 50), c["display"].lower(), c, f"plugins/{page.parent.name}/"))
    return [(c, href) for _, _, c, href in sorted(cards, key=lambda t: (t[0], t[1]))]


def render(catalog, entries, listed):
    mp = h(catalog["name"])
    first = entries[0] if entries else None
    points = "".join(f"<h3>{text(t)}</h3><p>{text(b)}</p>" for t, b in PILLARS)
    cards = "".join(card_html(c, href) for c, href in listed)
    cards += f"""
    <article class="card pcard soon" id="coming-soon">
      <h3>More plugins coming soon</h3>
      <p>Check back here</p>
    </article>"""
    return head("Exabeam Plug-in Catalog", catalog.get("metadata", {}).get("description", "")) + f"""{header(announcement(entries, ''))}

<main>
<section class="hero">
  {plugin_terminal(first, mp) if first else ''}
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="https://www.exabeam.com/">Home</a><span>&gt;</span><a href="#plugins">Plugins</a></nav>
    <h1>Exabeam plugins for AI agents</h1>
    <p>{text(HERO_LEAD)}</p>
    <div class="ctas"><a class="btn btn-green" href="#install">Get Started</a><a class="btn btn-line" href="#plugins">Browse Plugins</a></div>
    <div class="tags hero-tags"><span class="pill g">Stable, reviewed plug-ins</span><span class="pill">Governed by the Exabeam Enterprise Agreement</span></div>
  </div>
</section>
{subnav([("about", "About"), ("install", "Install"), ("plugins", "Plugins"), ("terms", "Terms")])}

<section class="benefits" id="about"><div class="wrap">
  <div><h2>The Exabeam Supported <span class="g">Plugin Marketplace</span></h2><p class="intro">{text(ABOUT_INTRO)}</p></div>
  <div class="points">{points}</div>
</div></section>

<section class="band-gray" id="install"><div class="wrap">
  <div class="center"><h2>Add the entire plugin catalog <span class="grad">at once</span></h2>
  <p>A single command adds Exabeam's entire Plug-in Catalog to your AI agent, so every supported plugin is available to you at once. It works the same way in Claude Code and OpenAI Codex.</p>
  <p>Each plugin has one install key in the format <code>&lt;plugin&gt;@{mp}</code>, and it's the same in both Claude Code and Codex. For example, the SOC plugin installs as <code>soc@{mp}</code> in either tool.</p></div>
  <div class="outlined">
    <div class="ocard"><button class="copy" type="button">Copy</button><h3>Claude Code</h3><pre>claude plugin marketplace add Exabeam-Labs/plugins</pre></div>
    <div class="ocard b"><button class="copy" type="button">Copy</button><h3>OpenAI Codex</h3><pre>codex plugin marketplace add Exabeam-Labs/plugins</pre></div>
  </div>
</div></section>

<section class="plugins" id="plugins"><div class="wrap">
  <div class="center"><h2>Plugin Catalog Listing</h2></div>
  <div class="cards">{cards}
  </div>
</div></section>

<section class="cta"><div class="wrap"><strong>Governed by the Exabeam Enterprise Agreement</strong><a class="btn btn-white" href="#terms">Read the Terms</a></div></section>

<section class="benefits terms" id="terms"><div class="wrap">
  <div><h2>Terms and <span class="g">licensing</span></h2></div>
  <div>
    <p>The plugins distributed here are <b>distributed under</b> the <a href="{TERMS_URL}">Exabeam Enterprise Agreement</a> and are subject to the same terms of use as the Exabeam products they work with. This marketplace is governed by the same agreement unless specified otherwise; each plugin carries its own <code>LICENSE</code> and <code>NOTICE</code>.</p>
    <p>A plugin may include open-source software. That software stays under its own license inside the distribution, its license text ships with the plugin, and the plugin's <code>LICENSE</code> names what it includes and where the source is also available (for the plugins here, through the <a href="{COMMUNITY_URL}">Open Agent and AI Security community</a>). The Enterprise Agreement governs this software distribution.</p>
  </div>
</div></section>
</main>

{footer('')}
<script>{LANDING_JS}{HEADER_JS}</script>
</body>
</html>
"""



def hand_authored(path):
    return path.exists() and not path.read_text(encoding="utf-8").startswith(MARK)


def outputs():
    """{path: html} for every page this build owns. A catalog plugin's page that someone hand-authored (no MARK) is
    left alone: it is not generated, and its own card JSON is what the listing uses."""
    catalog, entries = load()
    pages = {}
    for e in entries:
        detail = PLUGINS / e["name"] / "index.html"
        if not hand_authored(detail):
            pages[detail] = detail_page(catalog, e)
        pages.update(docs_pages(catalog, e))
    pages[PLUGINS / "_template" / "index.html"] = template_page()
    pages = {p: no_em_dashes(t) for p, t in pages.items() if not hand_authored(p)}
    listed = discover(pages)
    pages[OUT] = no_em_dashes(render(catalog, entries, listed))
    return entries, listed, pages


def orphans(pages):
    """Generated pages (they carry MARK) that this build no longer produces: a plugin left the catalog, or a doc page
    was renamed."""
    return sorted(p for p in PLUGINS.glob("**/index.html") if p not in pages and p.read_text(encoding="utf-8").startswith(MARK)) \
        if PLUGINS.is_dir() else []


def main(argv):
    entries, listed, pages = outputs()
    stale = orphans(pages)
    if "--check" in argv:
        bad = [p for p, t in pages.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        for p in bad + stale:
            print(f"{p.relative_to(ROOT)} is {'STALE' if p in bad else 'ORPHANED (no longer generated)'}")
        if bad or stale:
            print("regenerate with: python3 scripts/build_site.py"); return 1
        print(f"site is current with the catalog sources — {len(pages)} page(s), {len(listed)} card(s)"); return 0
    for p, t in pages.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:   # the same bytes on every platform
            f.write(t)
    for p in stale:
        p.unlink()
        d = p.parent
        while d != PLUGINS and d.is_dir() and not any(d.iterdir()):
            d.rmdir(); d = d.parent
    print(f"wrote {len(pages)} page(s) — {len(listed)} card(s): " + ", ".join(c["name"] for c, _ in listed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
