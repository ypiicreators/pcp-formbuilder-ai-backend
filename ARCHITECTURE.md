# AI-Based Form Builder — Detailed Architecture

**Project:** PCP Admin Portal — Form Builder AI Assist
**Source of truth:** *AI-Form-Builder-Plan-v2* (design/planning document)
**Purpose:** A detailed architectural reference derived from the plan. This document illustrates the target architecture of the feature — the components, boundaries, data flows, control flows, and contracts — using layered diagrams.

> This is a design artifact based on the plan, not on any current implementation state. Everything shown here reflects the intended architecture described in the plan.

---

## 1. System Context (C4 Level 1)

The feature spans three deployables: the admin browser (Form Builder), a dedicated AI backend service, and the citizen portal that renders the saved form. The LLM is an external, config-selected dependency.

```mermaid
flowchart TB
    Admin(["👤 Admin<br/>(form author)"])
    Citizen(["👤 Citizen<br/>(form filler)"])

    subgraph AdminPortal["pcp-admin-portal (browser SPA)"]
        FormBuilder["Form Builder + AI Assist"]
    end

    subgraph AIBackend["AI Backend Service (Python)"]
        AIService["Form-AI API<br/>+ orchestration<br/>+ validation"]
    end

    subgraph CitizenPortal["pcp-citizen-portal (browser SPA)"]
        Renderer["DynamicForm Renderer"]
    end

    LLM(["☁️ LLM Provider<br/>(config-selected:<br/>Azure OpenAI / OpenAI /<br/>Anthropic / Bedrock / Ollama)"])

    Admin -->|"describe / upload / edit + apply"| FormBuilder
    FormBuilder -->|"POST /api/form-ai/generate<br/>(mode + inputs)"| AIService
    AIService -->|"form JSON + warnings (+diff)"| FormBuilder
    AIService -->|"prompt (+ optional attachment)"| LLM
    LLM -->|"candidate JSON text"| AIService
    FormBuilder -.->|"exported / saved form JSON"| Renderer
    Citizen -->|"fills & submits"| Renderer

    classDef ext fill:#f5f5f5,stroke:#999,stroke-dasharray:4 3;
    class LLM,Admin,Citizen ext;
```

**Key boundary decision (plan §3):** the LLM call lives on the backend so the API key never ships in the browser bundle, validation is centralized, and providers can be swapped behind one interface.

---

## 2. Container / Component View (C4 Level 2)

The plan attaches the feature to real integration points. This view shows the concrete components on each side and how they connect.

```mermaid
flowchart LR
    subgraph Browser["Admin Browser — pcp-admin-portal"]
        direction TB
        Toolbar["formBuilder.tsx toolbar<br/>AI Assist button (showAIAssist)"]
        Modal["AI Assist Modal<br/>3 tabs: Describe / Upload / Edit JSON"]
        Progress["Progress stepper<br/>Analyzing → Generating → Validating → Ready"]
        ReviewGen["Read-only preview<br/>(generate modes)"]
        ReviewEdit["Monaco diff view<br/>(edit mode)"]
        Store["Zustand store<br/>loadFromJSON()"]
        Serializer["jsonGenerator.ts<br/>generateJSON()"]
        Upload["uploadBox / pre-signed URL"]

        Toolbar --> Modal
        Modal --> Upload
        Serializer --> Modal
        Modal --> Progress
        Progress --> ReviewGen
        Progress --> ReviewEdit
        ReviewGen -->|Apply| Store
        ReviewEdit -->|Apply| Store
        Store --> Toolbar
    end

    subgraph Backend["AI Backend Service"]
        direction TB
        API["API layer<br/>POST /api/form-ai/generate"]
        Graph["Orchestration graph<br/>router → prepare → generate<br/>→ apply → validate ⇄ repair → finalize"]
        ProvIface["LLM Provider Interface<br/>(config-selected adapter)"]
        Validator["Validation layer<br/>L1 structural + L2 business"]
        Prompts["Prompt builders<br/>system + per-mode + repair"]

        API --> Graph
        Graph --> Prompts
        Graph --> ProvIface
        Graph --> Validator
    end

    subgraph Citizen["pcp-citizen-portal"]
        Render["RenderField.tsx<br/>switch on field.type"]
    end

    Modal -->|"mode + inputs (JSON / multipart)"| API
    API -->|"form + warnings (+diff)"| Progress
    ProvIface -->|prompt + attachment| LLMExt(["☁️ LLM Provider"])
    Store -.->|saved form| Render

    classDef ext fill:#f5f5f5,stroke:#999,stroke-dasharray:4 3;
    class LLMExt ext;
```

### 2.1 Frontend integration points (plan §2.1, §5)

| Component | Role in the feature |
|---|---|
| `formBuilder.tsx` toolbar | Hosts the **AI Assist** button (after Export), driven by `showAIAssist` `useState` — mirrors `showImport`. |
| AI Assist modal | Three tabs, each with an **instruction text area**. |
| `generateJSON()` (`jsonGenerator.ts`) | Prefills the Edit-JSON tab. Note: output **starts at `sections`**, omits top-level metadata. |
| `uploadBox` / pre-signed URL | Reused for the document upload tab. |
| Monaco editor | Edit-mode diff (original vs modified). |
| `loadFromJSON()` (Zustand store) | The **only** path into the store; reused from manual JSON import. Re-derives `order`, autosaves `formBuilder_draft`. |

### 2.2 Backend components (plan §4)

| Component | Responsibility |
|---|---|
| API layer | Accepts JSON or multipart; enforces per-mode instruction rules; wraps in `apiPayload` envelope. |
| Orchestration graph | Directed node graph with a bounded validate→repair loop. |
| LLM Provider interface | Vendor-agnostic contract: text prompt in, clean JSON text out, optional attachment. |
| Validation layer | Two levels — structural (schema) + business rules. |
| Prompt builders | One shared system prompt + mode-specific user prompts + repair prompt. |

---

## 3. Backend Orchestration Graph (control flow)

The heart of the backend (plan §4.3–4.4). Each node does one job; a conditional edge implements the self-correcting **validate → repair** loop, hard-capped so it can never loop forever.

```mermaid
flowchart TD
    Start([Request received]) --> Router{Mode?}

    Router -->|generate_doc| Prep["Prepare document input<br/>attach-or-extract by<br/>provider capability"]
    Router -->|generate_nl| Gen
    Router -->|edit_json| Parse["Parse & verify base JSON<br/>is a valid form<br/>(incl. subSections)"]

    Prep --> Gen["Generate<br/>single LLM call via<br/>provider interface"]
    Parse --> Gen

    Gen --> ApplyCheck{Edit mode?}
    ApplyCheck -->|Yes| Apply["Apply change-set<br/>resolve field/section IDs<br/>onto base JSON"]
    ApplyCheck -->|No| Validate
    Apply --> Validate["Validate<br/>L1 schema + L2 business"]

    Validate --> Ok{Valid?}
    Ok -->|Yes| Finalize["Finalize<br/>emit loadFromJSON-ready form"]
    Ok -->|"No · attempts < max"| Repair["Repair<br/>LLM fixes only listed errors"]
    Ok -->|"No · attempts ≥ max"| Bail["Finalize partial<br/>+ warnings for human"]

    Repair --> Validate
    Finalize --> End([Return form + warnings + diff])
    Bail --> End

    classDef loop fill:#fff3e0,stroke:#e6820e;
    class Repair,Validate,Ok loop;
```

**Loop safety (plan §9):** the repair edge increments an attempt counter; once it hits `max`, the graph bails to a best-effort finalize with warnings rather than retrying forever.

---

## 4. LLM Provider Abstraction (plan §4.2)

The backend depends only on a narrow interface. The active provider is chosen by env (`LLM_PROVIDER`); switching = change env + restart, no feature-code change.

```mermaid
classDiagram
    class LLMProvider {
        <<interface>>
        +name: str
        +supports_document_input: bool
        +supports_json_mode: bool
        +generate(system_prompt, user_prompt, attachments?) str
    }

    class AzureOpenAIAdapter {
        +generate(...) str
        -strip_fences()
        -json_mode / temperature / retry
    }
    class OpenAIAdapter {
        +generate(...) str
    }
    class AnthropicAdapter {
        +generate(...) str
    }
    class BedrockAdapter {
        +generate(...) str
    }
    class OllamaAdapter {
        +generate(...) str
    }

    class ProviderFactory {
        +get_provider() LLMProvider
    }

    LLMProvider <|.. AzureOpenAIAdapter
    LLMProvider <|.. OpenAIAdapter
    LLMProvider <|.. AnthropicAdapter
    LLMProvider <|.. BedrockAdapter
    LLMProvider <|.. OllamaAdapter
    ProviderFactory --> LLMProvider : builds selected
```

**Adapter responsibilities (hidden behind the interface):**
- Normalize output to **clean JSON text** (strip markdown fences, unwrap envelope) so the validator always sees the same contract.
- Own vendor knobs: temperature, max tokens, JSON mode, retry/backoff, attachment encoding.
- Declare capabilities so the graph can branch (e.g. document attach vs text extraction).

```mermaid
flowchart LR
    Cap{"provider.supports<br/>_document_input?"}
    Cap -->|true| Attach["Forward file as attachment<br/>(pass-through node)"]
    Cap -->|false| Extract["Extract text first<br/>PyMuPDF / python-docx<br/>OCR fallback"]
    Attach --> Prompt["Build prompt<br/>(+attachment)"]
    Extract --> Prompt2["Build prompt<br/>(extracted text injected)"]
    Prompt --> Call["provider.generate()"]
    Prompt2 --> Call
```

> The **frontend contract does not change** regardless of provider capability.

---

## 5. Validation Layer (plan §4.5)

Two levels run before any output reaches the browser. Failures produce specific, path/id-tagged messages the repair step can act on.

```mermaid
flowchart TD
    In["Candidate form / applied change-set"] --> L1

    subgraph L1["Level 1 — Structural (schema shape)"]
        T1["field.type ∈ allowed FieldType"]
        T2["validator.type ∈ allowed ValidatorType"]
        T3["TableColumn.type ∈ smaller column set"]
        T4["every LocalizedText has non-empty en"]
        T5["select/radio/multiselect carry options"]
        T6["table/computed_table/verification carry columns"]
        T7["mobile_verification/in_form_login carry<br/>mobileNumberField + otpField"]
        T8["api_trigger carries api_config"]
        T9["traverse section.fields[] AND<br/>section.subSections[].fields[]"]
    end

    L1 --> L2

    subgraph L2["Level 2 — Business rules"]
        B1["field IDs unique across sections,<br/>subSections, AND nested ids"]
        B2["≤ 1 applying_for; id = applying_for_yourself"]
        B3["visibleWhen / hiddenWhen / disabledWhen /<br/>dependsOn / sourceField / formula /<br/>crossFieldValidators refs → existing ids"]
        B4["autoFillWhen / conditionalAutoFillWhen<br/>reference real fields"]
    end

    L2 --> Out{All pass?}
    Out -->|Yes| Pass["→ Finalize"]
    Out -->|No| Err["Error list with paths/ids<br/>→ Repair loop"]
```

**Why both levels matter (plan §2.1–2.2):**
- The manual import path (`handleImport`) does **no** structural check — this validation layer is the safety net.
- `RenderField.tsx` has no case for a hallucinated type, so it silently won't render — structural validation prevents that.
- The store only *warns* on duplicate top-level ids and never checks nested ids — business validation enforces true uniqueness.

> **Recommended:** generate the schema from `formBuilder.types.ts` so allowed lists and nested shapes never drift from the source of truth.

---

## 6. Frontend Flow (plan §5.6)

```mermaid
flowchart TD
    A[Admin clicks AI Assist] --> B[Modal opens]
    B --> C{Choose tab}
    C -->|Describe| D1["Type description<br/>(instruction = required)"]
    C -->|Upload| D2["Select PDF/DOCX<br/>(instruction optional)"]
    C -->|Edit JSON| D3["Prefill current JSON via generateJSON()<br/>+ type instruction (required)"]

    D1 --> E["Submit → POST /api/form-ai/generate"]
    D2 --> E
    D3 --> E

    E --> F["Progress:<br/>Analyzing → Generating → Validating"]
    F --> G{Response ok?}
    G -->|422 errors| H["Show errors<br/>admin retries/edits input"]
    G -->|200| I{Mode}
    I -->|Generate| J["Read-only preview"]
    I -->|Edit| K["Monaco diff view"]
    J --> L{Apply?}
    K --> L
    L -->|Discard| B
    L -->|Apply| M["loadFromJSON(approvedForm)"]
    M --> N["Canvas + live preview update"]
    N --> O["Admin fine-tunes by drag-and-drop"]
    O --> P["Save / export as today"]

    classDef human fill:#e8f5e9,stroke:#2e7d32;
    class J,K,L,O human;
```

**Human-in-the-loop (plan §5.4):** the AI never writes to the store. Preview/diff + **Apply** is the only path in. Because Apply overwrites the autosaved `formBuilder_draft`, the UI warns on unsaved work first.

---

## 7. End-to-End Sequence (all modes) (plan §6)

```mermaid
sequenceDiagram
    autonumber
    participant U as Admin
    participant FE as Form Builder (FE)
    participant BE as AI Backend
    participant P as LLM Provider
    participant V as Validation layer
    participant ST as Zustand store

    U->>FE: Click AI Assist, pick mode, provide input
    FE->>BE: POST /api/form-ai/generate (mode + inputs)

    alt Document mode
        BE->>BE: supports_document_input? attach file : extract text
    else Edit mode
        BE->>BE: Verify base JSON valid (incl. subSections)
    end

    BE->>P: generate(system, user, attachments?)
    P-->>BE: Candidate output (normalized JSON text)

    opt Edit mode
        BE->>BE: Apply ID-anchored change-set to base JSON
    end

    BE->>V: Validate (structural + business)
    loop while invalid and attempts remain
        V-->>BE: Errors (with paths/ids)
        BE->>P: Repair only these errors
        P-->>BE: Revised candidate
        BE->>V: Re-validate
    end
    V-->>BE: Valid (or best-effort + warnings)

    BE-->>FE: form JSON + warnings (+ diff)
    FE->>U: Preview / diff for review
    U->>FE: Apply
    FE->>ST: loadFromJSON(approvedForm)
    ST-->>FE: Canvas + live preview updated
    U->>FE: Edit manually, then save/export
```

---

## 8. API Contract & Data Model (plan §4.6–4.7)

### 8.1 Request

```mermaid
flowchart LR
    Req["POST /api/form-ai/generate<br/>(json or multipart)"]
    Req --> M["mode:<br/>generate_doc | generate_nl | edit_json"]
    Req --> F["file? (document mode)"]
    Req --> Pr["prompt? (instruction)"]
    Req --> BJ["base_json? (edit mode)"]

    subgraph Rules["Per-mode instruction rules"]
        R1["generate_nl → prompt REQUIRED"]
        R2["generate_doc → prompt OPTIONAL<br/>(server default if blank)"]
        R3["edit_json → prompt REQUIRED<br/>(else 422)"]
    end
    M -.-> Rules
```

### 8.2 Responses

| Status | Body | When |
|---|---|---|
| **200** | `{ form, warnings[], diff? }` | Success; `diff` present in edit mode only. |
| **422** | `{ errors[] }` | Unrecoverable after the repair budget. |

### 8.3 Output contract (flat, `loadFromJSON`-ready)

```mermaid
flowchart TD
    Form["form object (flat top-level)"]
    Form --> serviceCode
    Form --> title["title: LocalizedText"]
    Form --> description["description: LocalizedText"]
    Form --> category
    Form --> version
    Form --> jurisdiction
    Form --> sections["sections: Section[]"]
    Form --> cfv["crossFieldValidators[]"]

    sections --> Section
    Section --> sid["id"]
    Section --> stitle["title: LocalizedText"]
    Section --> sfields["fields: Field[]"]
    Section --> subs["subSections: SubSection[]"]
    subs --> subfields["fields: Field[] (same rules)"]

    sfields --> Field
    Field --> fid["id (globally unique)"]
    Field --> ftype["type ∈ FieldType"]
    Field --> flabel["label: LocalizedText (en required)"]
    Field --> nested["nested: options / columns /<br/>mobileNumberField+otpField / api_config"]
```

> `order` is omitted — the store re-derives it from array position. `subSections`, when present, obey all field rules.

---

## 9. Three Modes at a Glance (plan §1.1, §11)

```mermaid
flowchart LR
    subgraph ModeA["Describe (generate_nl)"]
        A1["Text description<br/>= the instruction"] --> A2["→ full form"]
    end
    subgraph ModeB["Upload (generate_doc)"]
        B1["PDF/DOCX + optional instruction"] --> B2["infer application form<br/>(not transcribe)"]
    end
    subgraph ModeC["Edit JSON (edit_json)"]
        C1["existing JSON + required instruction"] --> C2["ID-anchored change-set<br/>→ applied → diff"]
    end
```

Each mode uses the **shared system prompt** (schema rules) + a **mode-specific user prompt** (admin input). The repair loop uses a dedicated repair prompt seeded with the exact validation errors.

---

## 10. Cross-Cutting Concerns

```mermaid
mindmap
  root((AI Form Builder))
    Security
      LLM key server-side only
      No secret in browser bundle
      CORS restricted to admin origins
      apiPayload envelope + OrgId
    Reliability
      Bounded repair loop (hard cap)
      Best-effort bail + warnings
      Provider retry/backoff in adapter
      Nothing applied on failure
    Correctness
      Two-level validation gate
      Schema from types.ts (no drift)
      ID-anchored edits (no index miscount)
      applying_for singleton enforced
    Human control
      Preview / diff mandatory
      Apply is the only store write
      Warn on unsaved draft overwrite
    Extensibility
      Provider-agnostic interface
      Swap vendor by env, no code change
      Document input attach-or-extract
```

---

## 11. Component-to-Plan Traceability

| Architecture element | Plan section |
|---|---|
| System context / why separate backend | §3 |
| Frontend integration points | §2.1, §5 |
| Provider abstraction & capability branch | §2.3, §4.2 |
| Orchestration graph & node responsibilities | §4.3, §4.4 |
| Validation (structural + business) | §4.5 |
| Output contract (flat form) | §4.6 |
| API contract (request/response) | §4.7 |
| Frontend flow & review-before-apply | §5.3–5.6 |
| End-to-end sequence | §6 |
| Prompts (system / mode / repair) | §11 |
| Reliability & safety | §9 |

---

*This architecture document is derived from the AI-Form-Builder-Plan-v2 design artifact. It describes the intended architecture of the feature.*
