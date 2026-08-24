"""
services/knowledge_corpus.py

Phase 9A global knowledge corpus for ArtistHub AI.

This module defines the authoritative, curated knowledge base used by the
RAG retrieval layer (rag_retrieval.py).  Every record is a self-contained
passage sourced from a publicly accessible, authoritative organisation.

Design constraints
------------------
- Global knowledge only.  No private creator documents.
- No web-scraped blog posts or community forums.
- Every passage carries provenance metadata identifying the authoritative
  source organisation, document title, canonical URL, and authority tier.
- This file is static for Phase 9A.  Phase 9B will move to a vector store
  with runtime ingestion.

Knowledge domains covered (Phase 9A)
--------------------------------------
1. Performing Rights Organisations (PROs/CMOs) — ASCAP, BMI, SESAC, SOCAN
2. Mechanical royalties and The MLC
3. SoundExchange and neighbouring rights
4. Master vs. composition rights
5. ISRC and ISWC identifiers
6. Writer/publisher share structure
7. Copyright and DMCA fundamentals
8. Release and distribution preparation
9. ArtistHub platform help

Passage format
--------------
Each passage is a dict with:
    id:               Unique string identifier within this corpus.
    text:             Passage text (educational, ~200–400 words).
    source_org:       Authoritative source organisation name.
    document_title:   Title of the source document or page.
    source_url:       Canonical public URL for the source.
    ingestion_date:   ISO date this passage was added to the corpus.
    topic:            Domain topic tag.
    authority:        "primary_source" | "government" | "platform_doc".
    keywords:         List of lookup keywords to assist retrieval matching.

Retrieval note
--------------
Phase 9A uses simple keyword/BM25-style matching in rag_retrieval.py
rather than a vector index.  A production Phase 9B deployment would
replace this with an IBM watsonx.data vector index backed by IBM embedding
models.  The passage schema is designed to be forward-compatible with that
upgrade.
"""

from typing import List, Dict, Any

# ------------------------------------------------------------------ #
# Type alias                                                           #
# ------------------------------------------------------------------ #
KnowledgePassage = Dict[str, Any]


# ------------------------------------------------------------------ #
# Corpus — Phase 9A authoritative passages                            #
# ------------------------------------------------------------------ #

GLOBAL_KNOWLEDGE_CORPUS: List[KnowledgePassage] = [

    # ================================================================ #
    # 1. PROs / Performing Rights Organisations                         #
    # ================================================================ #
    {
        "id": "pro_overview_01",
        "text": (
            "A Performing Rights Organisation (PRO) — also called a Collective "
            "Management Organisation (CMO) outside the United States — licences "
            "the public performance of musical compositions on behalf of songwriters "
            "and music publishers. When a song is played on radio, streamed online, "
            "broadcast on TV, or performed in a venue, a performance royalty is owed "
            "to the composer(s) and publisher(s) of the composition. PROs collect "
            "these fees from broadcasters, venues, and streaming services, then "
            "distribute them to their members. "
            "In the United States the three major PROs are ASCAP (American Society of "
            "Composers, Authors and Publishers), BMI (Broadcast Music, Inc.), and "
            "SESAC (Society of European Stage Authors and Composers). An artist who "
            "writes their own songs should register with exactly one US PRO as a "
            "writer member. "
            "Performance royalties are split into a writer share and a publisher share. "
            "If you self-publish (own your own publishing), you can collect both "
            "shares by also registering a publishing entity with your PRO. Without a "
            "publishing administrator or your own publishing entity, the publisher "
            "share may go uncollected."
        ),
        "source_org": "ASCAP",
        "document_title": "What Is ASCAP? Understanding Performance Rights",
        "source_url": "https://www.ascap.com/help/royalties-and-licensing/what-is-ascap",
        "ingestion_date": "2025-01-15",
        "topic": "pro_royalties",
        "authority": "primary_source",
        "keywords": [
            "PRO", "CMO", "performing rights", "ASCAP", "BMI", "SESAC",
            "performance royalty", "songwriter", "publisher", "composition",
        ],
    },

    {
        "id": "ascap_bmi_sesac_compare_01",
        "text": (
            "ASCAP, BMI, and SESAC each collect performance royalties in the United "
            "States but differ in membership model, distribution speed, and repertoire. "
            "ASCAP is a member-owned non-profit; its board includes songwriters and "
            "publishers elected by members. BMI is a for-profit corporation but "
            "operates under a consent decree similar to ASCAP's. SESAC is a private "
            "company and membership is by invitation. All three license public "
            "performances and distribute royalties quarterly or semi-annually. "
            "A songwriter should choose one — you cannot be a writer member of more "
            "than one US PRO simultaneously. "
            "SOCAN is the Canadian equivalent. PRS for Music covers the UK. GEMA "
            "covers Germany. Each country typically has one dominant PRO that handles "
            "both domestic licensing and reciprocal international royalty collection "
            "via bilateral agreements between PROs worldwide."
        ),
        "source_org": "BMI",
        "document_title": "BMI 101: How BMI Works",
        "source_url": "https://www.bmi.com/creators",
        "ingestion_date": "2025-01-15",
        "topic": "pro_royalties",
        "authority": "primary_source",
        "keywords": [
            "ASCAP", "BMI", "SESAC", "SOCAN", "PRS", "GEMA",
            "PRO comparison", "songwriter registration", "performance royalty",
        ],
    },

    # ================================================================ #
    # 2. Mechanical Royalties and The MLC                               #
    # ================================================================ #
    {
        "id": "mechanical_royalties_01",
        "text": (
            "A mechanical royalty is owed to a songwriter and music publisher when "
            "their composition is reproduced — originally on physical media such as "
            "a vinyl record or CD (hence 'mechanical'), and today when it is "
            "reproduced in the form of an interactive digital stream or a downloaded "
            "file. "
            "In the United States, mechanical royalties for on-demand (interactive) "
            "streams are governed by Copyright Royalty Board (CRB) Phonorecords "
            "rates. Mechanical royalties differ from performance royalties: "
            "a performance royalty is paid when music is publicly performed (e.g. "
            "played on the radio), while a mechanical royalty is paid when the "
            "composition is reproduced in a copy (e.g. a stream that creates a "
            "server-side copy of the composition). "
            "Historically, mechanical royalties in the US were paid through the "
            "Harry Fox Agency (HFA). The Music Modernization Act of 2018 created "
            "The Mechanical Licensing Collective (The MLC) to administer blanket "
            "mechanical licenses for digital streaming services in the US. Songwriters "
            "and publishers must register with The MLC to receive mechanical royalties "
            "from streaming services that have obtained a blanket license."
        ),
        "source_org": "The Mechanical Licensing Collective (The MLC)",
        "document_title": "What Are Mechanical Royalties?",
        "source_url": "https://www.themlc.com/faq",
        "ingestion_date": "2025-01-15",
        "topic": "mechanical_royalties",
        "authority": "primary_source",
        "keywords": [
            "mechanical royalty", "MLC", "Mechanical Licensing Collective",
            "Music Modernization Act", "CRB", "Harry Fox", "streaming mechanical",
            "interactive stream", "reproduction right",
        ],
    },

    # ================================================================ #
    # 3. SoundExchange and Neighbouring Rights                          #
    # ================================================================ #
    {
        "id": "soundexchange_01",
        "text": (
            "SoundExchange is the US non-profit that collects and distributes digital "
            "performance royalties for sound recordings — as distinguished from "
            "composition royalties collected by PROs. When a sound recording is "
            "played on a non-interactive digital service (such as Pandora's "
            "ad-supported radio, satellite radio like SiriusXM, or internet radio "
            "stations), a neighbouring right performance royalty is owed to the "
            "recording artist and the rights holder of the master recording. "
            "SoundExchange splits this royalty 50/50: 45% to the featured artist and "
            "5% to non-featured session musicians, paid directly — bypassing the "
            "label. The remaining 50% goes to the master rights holder (usually the "
            "label, but an independent artist who owns their masters collects this "
            "portion too). "
            "Traditional AM/FM radio in the US does NOT pay performers a sound "
            "recording royalty — only a composition performance royalty to the PRO. "
            "Interactive on-demand streams (Spotify, Apple Music) do pay a master "
            "royalty, but that is paid by the DSP to the distributor/label, not "
            "through SoundExchange."
        ),
        "source_org": "SoundExchange",
        "document_title": "SoundExchange — How It Works",
        "source_url": "https://www.soundexchange.com/artist-copyright-owner/",
        "ingestion_date": "2025-01-15",
        "topic": "soundexchange_neighbouring_rights",
        "authority": "primary_source",
        "keywords": [
            "SoundExchange", "neighbouring rights", "digital performance royalty",
            "sound recording", "non-interactive", "Pandora", "SiriusXM",
            "internet radio", "master royalty", "featured artist",
        ],
    },

    # ================================================================ #
    # 4. Master vs. Composition Rights                                  #
    # ================================================================ #
    {
        "id": "master_vs_composition_01",
        "text": (
            "Every song involves two separate copyrights: the composition (also called "
            "the musical work) and the sound recording (also called the master). "
            "The COMPOSITION copyright covers the underlying melody and lyrics. It "
            "is typically owned by the songwriter(s) and/or their music publisher. "
            "Composition royalties flow from PROs (performance royalties) and The MLC "
            "or distributors (mechanical royalties). "
            "The MASTER (sound recording) copyright covers the specific recorded "
            "performance. It is typically owned by whoever funded and produced the "
            "recording — traditionally a record label, but an independent artist who "
            "funds their own sessions owns their masters. Master royalties include "
            "the label's share from streaming, sync licensing fees for the recording, "
            "and neighbouring rights from non-interactive digital services via "
            "SoundExchange. "
            "When a song is licensed for a film or TV show (sync license), both a "
            "sync fee for the master and a sync fee for the composition are typically "
            "negotiated separately. Owning your masters gives you full control over "
            "how the recording is used, licensed, and monetised."
        ),
        "source_org": "US Copyright Office",
        "document_title": "Copyright in Music: Circular 56",
        "source_url": "https://www.copyright.gov/circs/circ56.pdf",
        "ingestion_date": "2025-01-15",
        "topic": "master_composition_rights",
        "authority": "government",
        "keywords": [
            "master rights", "composition rights", "sound recording copyright",
            "musical work", "sync license", "publishing", "independent artist",
            "own your masters", "copyright ownership",
        ],
    },

    # ================================================================ #
    # 5. Writer/Publisher Share                                         #
    # ================================================================ #
    {
        "id": "writer_publisher_share_01",
        "text": (
            "When a PRO distributes performance royalties, they are divided into a "
            "writer share (50%) and a publisher share (50%). The total royalty is "
            "100%, of which the writer receives half and the publisher receives half. "
            "If a songwriter is not affiliated with a publisher or has not set up "
            "their own publishing entity, the publisher share may go uncollected or "
            "be held by the PRO until claimed. "
            "A songwriter who also controls their own publishing (common for independent "
            "artists) can register both as a writer member and establish a publishing "
            "entity with the PRO to collect both the writer and publisher shares. "
            "Co-written songs split the writer shares among contributors according "
            "to an agreed split. For example, two equal co-writers each receive 25% "
            "(their 50% of the 50% writer share). The publisher share is split "
            "proportionally based on each writer's publishing arrangement. "
            "Split sheets are agreements between co-writers documenting the agreed "
            "percentage split before release."
        ),
        "source_org": "ASCAP",
        "document_title": "Understanding Writer and Publisher Shares",
        "source_url": "https://www.ascap.com/help/royalties-and-licensing/royalties-overview",
        "ingestion_date": "2025-01-15",
        "topic": "writer_publisher_share",
        "authority": "primary_source",
        "keywords": [
            "writer share", "publisher share", "co-writer", "split sheet",
            "publishing entity", "self-publishing", "royalty split",
            "50/50 split", "publishing administration",
        ],
    },

    # ================================================================ #
    # 6. ISRC and ISWC Identifiers                                      #
    # ================================================================ #
    {
        "id": "isrc_01",
        "text": (
            "An ISRC (International Standard Recording Code) is a unique 12-character "
            "alphanumeric identifier assigned to a specific sound recording (master). "
            "It identifies the recording — not the composition. Each unique version "
            "of a recording (e.g. the album version vs. the radio edit) should have "
            "its own ISRC. "
            "ISRCs are essential for digital distribution: DSPs (Spotify, Apple Music, "
            "Amazon Music, etc.) use ISRCs to track plays and attribute streaming "
            "royalties. Without a correct ISRC, royalty reporting may be inaccurate. "
            "Most digital distributors (DistroKid, TuneCore, CD Baby, etc.) assign "
            "ISRCs automatically when you upload a release. You can also obtain a "
            "national registrant code from your country's ISRC national agency and "
            "assign codes yourself. In the US, the Recording Industry Association of "
            "America (RIAA) manages ISRC registrant codes."
        ),
        "source_org": "IFPI / RIAA",
        "document_title": "ISRC — International Standard Recording Code",
        "source_url": "https://www.riaa.com/resources-learning/isrc/",
        "ingestion_date": "2025-01-15",
        "topic": "isrc_iswc",
        "authority": "primary_source",
        "keywords": [
            "ISRC", "International Standard Recording Code", "recording identifier",
            "DSP", "streaming royalties", "RIAA", "IFPI", "distributor",
            "digital distribution",
        ],
    },

    {
        "id": "iswc_01",
        "text": (
            "An ISWC (International Standard Musical Work Code) is a unique identifier "
            "assigned to a musical composition (the work, not the recording). It is "
            "analogous to an ISBN for books. While ISRCs identify recordings, ISWCs "
            "identify compositions. "
            "PROs use ISWCs to match compositions registered in their database to "
            "performances and streams reported by broadcasters and DSPs. Registering "
            "your composition with your PRO typically results in an ISWC being "
            "assigned. The ISWC is managed by the ISWC International Agency. "
            "Having an ISWC helps ensure accurate royalty matching across international "
            "PROs when your music is performed in other countries — reciprocal "
            "agreements between PROs depend on reliable work identifiers."
        ),
        "source_org": "CISAC / ISWC International Agency",
        "document_title": "ISWC — International Standard Musical Work Code",
        "source_url": "https://www.iswc.org/en/faq.html",
        "ingestion_date": "2025-01-15",
        "topic": "isrc_iswc",
        "authority": "primary_source",
        "keywords": [
            "ISWC", "International Standard Musical Work Code", "composition identifier",
            "musical work", "CISAC", "PRO registration", "international royalties",
        ],
    },

    # ================================================================ #
    # 7. Copyright and DMCA Fundamentals                                #
    # ================================================================ #
    {
        "id": "copyright_basics_01",
        "text": (
            "In the United States, copyright protection attaches automatically to an "
            "original creative work at the moment it is fixed in a tangible medium of "
            "expression — such as a recorded song or written lyric sheet. Registration "
            "with the US Copyright Office is not required for protection to exist, "
            "but registration provides important legal benefits: it creates a public "
            "record of ownership, is required before filing an infringement lawsuit "
            "in federal court, and — if registered before infringement or within three "
            "months of publication — entitles the owner to statutory damages and "
            "attorney's fees. "
            "The current term of copyright for works created after January 1, 1978 "
            "is the life of the author plus 70 years. For works made for hire, "
            "anonymous works, or pseudonymous works, the term is 95 years from "
            "publication or 120 years from creation, whichever expires first. "
            "The Copyright Office online registration portal (copyright.gov/registration) "
            "accepts applications for both sound recordings (Form SR) and musical "
            "compositions (Form PA or short Form PA)."
        ),
        "source_org": "US Copyright Office",
        "document_title": "Copyright Basics: Circular 1",
        "source_url": "https://www.copyright.gov/circs/circ01.pdf",
        "ingestion_date": "2025-01-15",
        "topic": "copyright",
        "authority": "government",
        "keywords": [
            "copyright", "copyright registration", "US Copyright Office",
            "copyright term", "infringement", "statutory damages", "fair use",
            "copyright basics", "sound recording copyright", "musical work copyright",
        ],
    },

    {
        "id": "dmca_basics_01",
        "text": (
            "The Digital Millennium Copyright Act (DMCA) of 1998 is the primary US law "
            "governing copyright in the digital environment. Key provisions relevant "
            "to independent musicians include: "
            "1. DMCA Takedown (Section 512): If someone posts your copyrighted music "
            "without permission on a platform (YouTube, SoundCloud, etc.), you can "
            "send a DMCA takedown notice to the platform's designated copyright agent. "
            "The platform must remove the content expeditiously or lose safe harbour "
            "protection. The alleged infringer may file a counter-notice to dispute "
            "the takedown. "
            "2. Content ID (YouTube): YouTube's Content ID system is a separate "
            "automated rights management tool; it is not the same as a DMCA notice. "
            "Rights holders enrolled in Content ID can have their music automatically "
            "identified and monetised or blocked on YouTube. "
            "3. Anti-circumvention (Section 1201): Prohibits circumventing "
            "technological protection measures (DRM) on copyrighted works."
        ),
        "source_org": "US Copyright Office",
        "document_title": "DMCA — Digital Millennium Copyright Act Overview",
        "source_url": "https://www.copyright.gov/dmca/",
        "ingestion_date": "2025-01-15",
        "topic": "copyright",
        "authority": "government",
        "keywords": [
            "DMCA", "takedown", "Section 512", "safe harbour", "counter-notice",
            "Content ID", "YouTube", "copyright infringement", "digital copyright",
        ],
    },

    # ================================================================ #
    # 8. Release and Distribution Preparation                           #
    # ================================================================ #
    {
        "id": "release_prep_01",
        "text": (
            "Before distributing a release to streaming services, independent artists "
            "should complete the following checklist to ensure proper royalty routing: "
            "1. Register with a PRO (ASCAP, BMI, or SESAC in the US) as a songwriter "
            "and set up a publishing entity if you self-publish. "
            "2. Register your songs with your PRO so performance royalties can be "
            "collected when the tracks are streamed or broadcast. "
            "3. Register as a recipient with The MLC to collect US mechanical "
            "royalties from streaming services. "
            "4. Register with SoundExchange to collect digital performance royalties "
            "for non-interactive streams (internet radio, satellite radio). "
            "5. Ensure each recording has a correct ISRC assigned (your distributor "
            "can do this). "
            "6. Ensure each composition has an ISWC (register with your PRO). "
            "7. Prepare metadata: track titles, featured artists, release date, "
            "UPC/EAN barcode, genre, and explicit content flag. "
            "8. Verify artwork meets DSP specifications (typically 3000×3000 px, "
            "JPEG or PNG, no URLs or pricing in the artwork). "
            "9. Deliver audio in the format required by your distributor (typically "
            "WAV 16-bit/44.1kHz minimum; 24-bit/48kHz preferred)."
        ),
        "source_org": "ArtistHub Platform Documentation",
        "document_title": "Release Preparation Checklist for Independent Artists",
        "source_url": "https://artisthub.example.com/help/release-prep",
        "ingestion_date": "2025-01-15",
        "topic": "release_distribution",
        "authority": "platform_doc",
        "keywords": [
            "release preparation", "distribution", "checklist", "DSP", "metadata",
            "UPC", "barcode", "artwork", "WAV", "audio format", "distributor",
            "PRO registration", "MLC registration", "SoundExchange",
        ],
    },

    # ================================================================ #
    # 9. ArtistHub Platform Help                                        #
    # ================================================================ #
    {
        "id": "artisthub_help_01",
        "text": (
            "ArtistHub is a platform for independent musicians to share their music, "
            "connect with fans, and sell releases and merchandise. "
            "Artist accounts allow you to: create a profile, upload music releases, "
            "publish social posts to your followers, and list merchandise for sale. "
            "Fan accounts allow you to: browse and follow artists, read posts in your "
            "feed, and purchase releases and merchandise. "
            "All purchases on ArtistHub are simulated in the MVP — no real payment "
            "processor is integrated. Orders are recorded in the platform for "
            "demonstration purposes. "
            "ArtistHub AI is a read-only educational assistant. It can help you "
            "understand music business concepts, royalties, rights, and release "
            "preparation. It cannot modify your profile, releases, posts, or merch "
            "listings, and it is not a substitute for legal, accounting, or financial "
            "professional advice."
        ),
        "source_org": "ArtistHub Platform Documentation",
        "document_title": "ArtistHub Help Centre — Getting Started",
        "source_url": "https://artisthub.example.com/help",
        "ingestion_date": "2025-01-15",
        "topic": "artisthub_help",
        "authority": "platform_doc",
        "keywords": [
            "ArtistHub", "platform", "artist account", "fan account",
            "releases", "merchandise", "social posts", "ArtistHub AI",
            "help", "getting started",
        ],
    },
]


# ------------------------------------------------------------------ #
# Accessor                                                             #
# ------------------------------------------------------------------ #

def get_corpus() -> List[KnowledgePassage]:
    """
    Return the full Phase 9A global knowledge corpus.

    Returns:
        List of KnowledgePassage dicts.  Read-only; callers must not mutate.
    """
    return GLOBAL_KNOWLEDGE_CORPUS
