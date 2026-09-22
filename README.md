# Ally

### From conversation to shared understanding

Ally is a secure, multilingual meeting-intelligence platform designed to help diverse teams communicate clearly, preserve essential context, and turn conversations into accountable action.

The platform transforms meeting transcripts, recordings, and documents into structured, role-aware summaries. It identifies proposed action items, highlights responsibilities and deadlines, and presents every AI-generated task for human review before it can enter the reminder workflow.

By combining multilingual support with responsible automation, Ally helps teams overcome language barriers without sacrificing accuracy, ownership, or human judgement.

## The vision

Important decisions are often buried in long meetings, fragmented notes, and conversations involving people with different linguistic or professional backgrounds. Valuable context can be lost, responsibilities can become unclear, and agreed actions may never be completed.

Ally addresses this problem by creating a shared layer of understanding between conversation and execution. Its purpose is not simply to summarise what was said, but to help every participant understand what matters, what was decided, and what should happen next.

## Core capabilities

- **Multilingual intelligence** — Translate and summarise content for teams working across languages.
- **Role-aware summaries** — Present information according to the needs of managers, engineers, designers, stakeholders, and other participants.
- **Document and transcript processing** — Analyse typed content, uploaded documents, recorded audio, and meeting transcripts.
- **Structured meeting outcomes** — Produce clear summaries, decisions, key themes, and suggested next steps.
- **Reviewable action items** — Extract proposed tasks with assignees, dates, recipient details, and supporting source evidence.
- **Human approval workflow** — Require explicit confirmation before an AI-generated action becomes eligible for reminders.
- **Secure reminders** — Deliver controlled, traceable reminders while preventing duplicate or unauthorised sends.
- **Meeting history** — Preserve owner-scoped records for future reference and accountability.
- **Document Q&A and topic detection** — Explore long content through focused questions and structured topic navigation.

## Trust by design

Ally treats AI output as a proposal rather than an unquestionable source of truth.

Model responses are validated against strict application schemas, generated HTML is escaped locally, provider configuration remains server-controlled, and sensitive credentials are never exposed to the browser. AI-derived actions begin in a pending-review state and cannot trigger reminders until they have been reviewed and confirmed by their authenticated owner.

These safeguards reduce the consequences of inaccurate or manipulated model output while keeping the user in control of consequential decisions.

## Technology

Ally is built with:

- **FastAPI and Python** for authenticated backend services
- **Groq** for backend-only text generation and structured AI output
- **AssemblyAI** for speech transcription
- **Supabase** for authentication, PostgreSQL persistence, row-level security, and realtime data
- **QStash** for signed scheduled reminder execution
- **Brevo** for transactional email delivery
- **JavaScript and Tailwind CSS** for the browser experience

## Responsible AI

AI-generated summaries and action items may contain mistakes or omissions. Ally therefore applies bounded input processing, strict output validation, server-owned provenance, durable request idempotency, and mandatory human review before generated actions can cause reminder side effects.

The system is designed to support human judgement—not replace it.

## Project direction

Ally aims to make collaboration more inclusive, decisions more visible, and commitments more accountable. Its long-term vision is a workplace in which language differences do not prevent people from contributing fully or understanding the decisions that affect them.

> One conversation. Shared understanding. Accountable action.
