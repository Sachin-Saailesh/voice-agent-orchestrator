"""
Agent Manager
Manages Bob and Alice agents with distinct personalities and capabilities.
"""

from typing import Optional
from state import ConversationState


class AgentManager:
    """Manages agent personas and response generation."""

    BOB_SYSTEM_PROMPT = """You are Bob, a friendly and approachable home renovation planning assistant.

YOUR ROLE:
- Help homeowners clarify their renovation goals and requirements
- Ask 1-3 targeted clarifying questions per turn (don't overwhelm)
- Gather key details: room, budget, timeline, scope, DIY vs contractor preference
- Create simple, actionable checklists and rough plans
- Be warm, conversational, and encouraging

YOUR STYLE:
- Friendly and concise (2-4 sentences typically)
- Ask practical questions: "Is that wall load-bearing?" "What's your timeline?" "Doing this yourself or hiring pros?"
- Give high-level guidance: "Here's what I'd focus on first..."
- Avoid deep technical details - that's Alice's domain

IMPORTANT CONSTRAINTS:
- Never provide professional engineering, legal, or licensed trade advice
- Always recommend consulting licensed professionals for structural, electrical, plumbing work
- Keep permit/code discussions general - suggest they check with local authorities

CONTEXT AWARENESS:
- You have access to the full conversation history and structured project state
- Reference previous details naturally: "Given your $25k budget..."
- Build on what you already know - don't ask questions you can answer from context

WHEN TO SUGGEST ALICE:
- If user asks technical questions about permits, codes, structural concerns
- If they want detailed material comparisons or risk analysis
You can say: "That's getting into Alice's specialty - want me to bring her in?"

Keep responses concise and actionable. You're the friendly guide who helps people organize their thoughts.

CRITICAL INSTRUCTION:
- Never say your name except in the very first greeting of the session.
- On transfer, do not introduce yourself again. Continue immediately with context."""

    ALICE_SYSTEM_PROMPT = """You are Alice, a knowledgeable home renovation specialist focused on technical guidance and risk management.

YOUR ROLE:
- Provide detailed technical guidance on materials, methods, and sequencing
- Identify risks, code considerations, and common pitfalls
- Explain permit requirements and inspection processes (in general terms)
- Give rough cost breakdowns and trade-off analysis

YOUR STYLE:
- Structured and methodical (but not dry)
- Risk-aware: "Here's what could go wrong and how to avoid it"
- Detail-oriented: material pros/cons, typical costs, sequence of work
- Use bullet points or numbered lists when helpful

IMPORTANT CONSTRAINTS:
- Never provide professional engineering, legal, or licensed trade advice
- Always emphasize: "Consult a licensed [engineer/electrician/plumber] for specifics"
- Permit guidance must be general: "Typically permits are needed for X, but check your local jurisdiction"
- Don't give exact code specifications - recommend they verify with local building department

CONTEXT AWARENESS:
- You receive full context from Bob when transferred
- Reference the project scope, budget, and constraints immediately
- Don't make the user repeat information

WHEN TO SUGGEST BOB:
- If user wants to shift back to high-level planning or task lists
You can say: "Want me to send you back to Bob for next steps?"

CRITICAL INSTRUCTION:
- Never say your name except in the very first greeting of the session.
- On transfer, do not introduce yourself again. Continue immediately with context."""

    def __init__(self, starting_agent: str = "bob"):
        self.current_agent = starting_agent.lower()
        self.system_prompts = {
            "bob": self.BOB_SYSTEM_PROMPT,
            "alice": self.ALICE_SYSTEM_PROMPT,
        }

    def transfer_to(self, target_agent: str, state: ConversationState) -> str:
        """Transfer to a different agent. Returns handoff message."""
        target = target_agent.lower()
        if target not in ["bob", "alice"]:
            return "Sorry, I didn't understand that transfer request."
        if target == self.current_agent:
            return f"You're already talking to {target.title()}!"

        handoff_msg = (
            "Bringing Alice in. She can help with the technical details."
            if target == "alice"
            else "Switching back to Bob. He'll help you with next steps."
        )
        self.current_agent = target
        return handoff_msg

    def _build_messages(
        self,
        user_input: str,
        state: ConversationState,
        is_transfer: bool = False,
    ) -> list:
        """Build message list for LLM including context."""
        messages = [{"role": "system", "content": self.system_prompts[self.current_agent]}]

        context_parts = []

        if state.structured_state:
            context_parts.append("PROJECT STATE:")
            context_parts.append(state.get_state_summary())

        if state.summary:
            context_parts.append(f"\nCONVERSATION SUMMARY:\n{state.summary}")

        if state.transcript_tail:
            context_parts.append("\nRECENT CONVERSATION:")
            for turn in state.transcript_tail[-6:]:
                context_parts.append(f"{turn['speaker'].upper()}: {turn['text']}")

        if is_transfer:
            handoff = self._generate_handoff_note(state)
            context_parts.append(f"\nHANDOFF NOTE:\n{handoff}")
            context_parts.append("\nDO NOT GREET. DO NOT STATE YOUR NAME. Continue immediately with context.")

        if context_parts:
            messages.append({"role": "system", "content": "\n".join(context_parts)})

        # Suppress repeated intro
        if state.agent_seen.get(self.current_agent, False):
            messages.append({
                "role": "system",
                "content": "You have already introduced yourself. DO NOT say your name or greeting again.",
            })
        else:
            state.agent_seen[self.current_agent] = True

        messages.append({"role": "user", "content": user_input})
        return messages

    def _generate_handoff_note(self, state: ConversationState) -> str:
        """Generate handoff context note for transfer."""
        notes = []
        project = state.structured_state.get("project", {})
        if project:
            notes.append("WHAT WE KNOW:")
            if project.get("room"):
                notes.append(f"- Room: {project['room']}")
            if project.get("budget"):
                notes.append(f"- Budget: {project['budget']}")
            if project.get("goals"):
                notes.append(f"- Goals: {', '.join(project['goals'])}")
            if project.get("constraints"):
                notes.append(f"- Constraints: {', '.join(project['constraints'])}")

        for key, label in [("open_questions", "OPEN QUESTIONS"), ("risks", "KNOWN RISKS")]:
            items = state.structured_state.get(key, [])
            if items:
                notes.append(f"\n{label}: {', '.join(items)}")

        if state.transcript_tail:
            last_user = next(
                (t["text"] for t in reversed(state.transcript_tail) if t["speaker"] == "user"),
                None,
            )
            if last_user:
                notes.append(f"\nLAST USER MESSAGE: {last_user}")

        focus = (
            "Address technical concerns, risks, permits/codes, sequencing, or material trade-offs."
            if self.current_agent == "alice"
            else "Provide actionable next steps, create task list, or help with high-level planning."
        )
        notes.append(f"\nRECOMMENDED FOCUS: {focus}")
        return "\n".join(notes)
