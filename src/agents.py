"""
Agent Manager
Manages Bob and Alice agents with distinct personalities and capabilities
"""

from typing import Optional, Dict, Any
from state import ConversationState
from llm import LLMClient


class AgentManager:
    """Manages agent personas and response generation"""
    
    # Bob's system prompt - Intake & Planning
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
- Be realistic about costs and timelines

CONTEXT AWARENESS:
- You have access to the full conversation history and structured project state
- Reference previous details naturally: "Given your $25k budget..."
- Build on what you already know - don't ask questions you can answer from context

WHEN TO SUGGEST ALICE:
- If user asks technical questions about permits, codes, structural concerns
- If they want detailed material comparisons or risk analysis
- If they ask about inspection requirements or sequencing complex work
You can say: "That's getting into Alice's specialty - want me to bring her in?"

Keep responses concise and actionable. You're the friendly guide who helps people organize their thoughts.

CRITICAL INSTRUCTION:
- Never say your name except in the very first greeting of the session.
- On transfer, do not introduce yourself again. Continue immediately with context."""

    # Alice's system prompt - Specialist & Risk Analysis
    ALICE_SYSTEM_PROMPT = """You are Alice, a knowledgeable home renovation specialist focused on technical guidance and risk management.

YOUR ROLE:
- Provide detailed technical guidance on materials, methods, and sequencing
- Identify risks, code considerations, and common pitfalls
- Explain permit requirements and inspection processes (in general terms)
- Give rough cost breakdowns and trade-off analysis
- Help users understand what to expect and what to watch out for

YOUR STYLE:
- Structured and methodical (but not dry)
- Risk-aware: "Here's what could go wrong and how to avoid it"
- Detail-oriented: material pros/cons, typical costs, sequence of work
- Use bullet points or numbered lists when helpful
- Slightly more formal than Bob, but still accessible

IMPORTANT CONSTRAINTS:
- Never provide professional engineering, legal, or licensed trade advice
- Always emphasize: "Consult a licensed [engineer/electrician/plumber] for specifics"
- Permit guidance must be general: "Typically permits are needed for X, but check your local jurisdiction"
- Don't give exact code specifications - recommend they verify with local building department
- Be clear about what requires professional assessment (structural, electrical, gas, etc.)

CONTEXT AWARENESS:
- You receive full context from Bob when transferred
- Reference the project scope, budget, and constraints immediately
- Continue the conversation naturally: "I see you're working with $25k for the kitchen..."
- Don't make the user repeat information

WHEN TO SUGGEST BOB:
- If user wants to shift back to high-level planning or task lists
- If they want homeowner-friendly next steps
- If the conversation is wrapping up and they need an action plan
You can say: "Want me to send you back to Bob for next steps?"

Provide actionable technical guidance while being clear about professional boundaries. You're the knowledgeable specialist who helps people understand complexity.

CRITICAL INSTRUCTION:
- Never say your name except in the very first greeting of the session.
- On transfer, do not introduce yourself again. Continue immediately with context."""

    def __init__(self, starting_agent: str = "bob"):
        """Initialize with starting agent"""
        self.current_agent = starting_agent.lower()
        self.system_prompts = {
            "bob": self.BOB_SYSTEM_PROMPT,
            "alice": self.ALICE_SYSTEM_PROMPT
        }
    
    def transfer_to(self, target_agent: str, state: ConversationState) -> str:
        """
        Transfer to a different agent
        Returns handoff message to speak to user
        """
        target = target_agent.lower()
        
        if target not in ["bob", "alice"]:
            return "Sorry, I didn't understand that transfer request."
        
        if target == self.current_agent:
            return f"You're already talking to {target.title()}!"
        
        # Generate handoff message
        if target == "alice":
            handoff_msg = "Bringing Alice in. She can help with the technical details."
        else:  # bob
            handoff_msg = "Switching back to Bob. He'll help you with next steps."
        
        # Update current agent
        self.current_agent = target
        
        return handoff_msg
    
    def get_response(
        self,
        user_input: str,
        state: ConversationState,
        llm_client: LLMClient,
        is_transfer_continuation: bool = False
    ) -> str:
        """
        Generate agent response
        
        Args:
            user_input: User's message
            state: Conversation state
            llm_client: LLM client
            is_transfer_continuation: True if this is first message after transfer
        
        Returns:
            Agent's response text
        """
        # Build context for LLM
        messages = self._build_messages(user_input, state, is_transfer_continuation)
        
        # Get response from LLM
        # Explicitly request gpt-4o-mini for speed
        response = llm_client.chat(
            messages, 
            model="gpt-4o-mini",
            max_tokens=400, 
            temperature=0.7
        )
        
        if response:
            return response
        else:
            # Fallback response
            return "I'm having trouble processing that right now. Could you try rephrasing?"
    
    def _build_messages(
        self,
        user_input: str,
        state: ConversationState,
        is_transfer: bool
    ) -> list:
        """Build message list for LLM including context"""
        messages = []
        
        # System prompt for current agent
        messages.append({
            "role": "system",
            "content": self.system_prompts[self.current_agent]
        })
        
        # Add structured state context
        context_parts = []
        
        # Project state
        if state.structured_state:
            context_parts.append("PROJECT STATE:")
            context_parts.append(state.get_state_summary())
        
        # Rolling summary
        if state.summary:
            context_parts.append(f"\nCONVERSATION SUMMARY:\n{state.summary}")
        
        # Recent transcript
        if state.transcript_tail:
            context_parts.append("\nRECENT CONVERSATION:")
            for turn in state.transcript_tail[-6:]:  # Last 6 turns (3 exchanges)
                speaker = turn['speaker'].upper()
                text = turn['text']
                context_parts.append(f"{speaker}: {text}")
        
        # Add handoff note if this is a transfer
        if is_transfer:
            handoff = self._generate_handoff_note(state)
            context_parts.append(f"\nHANDOFF NOTE:\n{handoff}")
            context_parts.append("\nContinue the conversation naturally with full context.")
            context_parts.append("DO NOT GREET. DO NOT STATE YOUR NAME. Continue immediately where the previous agent left off.")
        
        # Combine all context
        if context_parts:
            context_message = "\n".join(context_parts)
            messages.append({
                "role": "system",
                "content": context_message
            })
        
        # Add intro suppression if needed
        self._add_intro_instruction(messages, state)
        
        # Add user's current input
        messages.append({
            "role": "user",
            "content": user_input
        })
        
        return messages
        
    def _add_intro_instruction(self, messages: list, state: ConversationState):
        """Add instruction to suppress intro if agent seen"""
        if state.agent_seen.get(self.current_agent, False):
            messages.append({
                "role": "system",
                "content": "You have already introduced yourself in this session. DO NOT say your name or greeting again. Just continue the conversation."
            })
        else:
            # Mark as seen for next time (in-memory update, will be persisted when state updates)
            state.agent_seen[self.current_agent] = True
    
    def _generate_handoff_note(self, state: ConversationState) -> str:
        """Generate handoff note for transfer"""
        notes = []
        
        # What we know
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
        
        # Open questions and risks
        open_q = state.structured_state.get("open_questions", [])
        risks = state.structured_state.get("risks", [])
        
        if open_q:
            notes.append(f"\nOPEN QUESTIONS: {', '.join(open_q)}")
        
        if risks:
            notes.append(f"\nKNOWN RISKS: {', '.join(risks)}")
        
        # Last user concern
        if state.transcript_tail:
            last_user = next(
                (t['text'] for t in reversed(state.transcript_tail) if t['speaker'] == 'user'),
                None
            )
            if last_user:
                notes.append(f"\nLAST USER MESSAGE: {last_user}")
        
        # Recommended focus
        if self.current_agent == "alice":
            notes.append("\nRECOMMENDED FOCUS: Address technical concerns, risks, permits/codes (if relevant), sequencing, or material trade-offs.")
        else:
            notes.append("\nRECOMMENDED FOCUS: Provide actionable next steps, create task list, or help with high-level planning.")
        
        return "\n".join(notes)



