# Policy: Support SLA

**Document ID:** SUP-001
**Category:** Support
**Last Updated:** 2025-09-15
**Owner:** Customer Support

## Severity Levels
- **Sev-1 (Critical):** Production outage or data loss. First response: 1
  hour (Enterprise), 4 hours (Business), best-effort (Starter).
- **Sev-2 (High):** Major feature unusable, no workaround. First response:
  4 hours (Enterprise), 1 business day (Business), 2 business days (Starter).
- **Sev-3 (Normal):** Minor issue or question, workaround available. First
  response: 1 business day (Enterprise), 2 business days (Business/Starter).

## Ticket Lifecycle
1. **open** — newly created, awaiting first response.
2. **in_progress** — actively being worked by Support.
3. **waiting_on_customer** — Support has responded and needs customer input.
4. **resolved** — issue fixed; ticket auto-closes after 7 days of inactivity.
5. **closed** — ticket lifecycle complete.

## Escalation
Tickets that breach their SLA first-response window are automatically
escalated to the Support team lead. Sev-1 tickets that remain open for more
than 4 hours are escalated to the Engineering on-call rotation.

## Creating Tickets on Behalf of Customers
Support staff and authorized AI assistants may create tickets on behalf of a
customer when a documented issue is reported. Ticket creation is considered
a data-modifying action and requires explicit human confirmation before
being finalized, per the Security and AI Usage Policy (SEC-002).
