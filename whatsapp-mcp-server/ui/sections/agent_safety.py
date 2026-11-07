"""Safety of the agent: read-only mode, session taint and how long drafts live (environment variables of the MCP server).

The values and their meaning come from server_mode.py, taint.py and drafts.py. A value that weakens a defence
carries a risk text, and `risk_when` names the values it applies to.
"""
import drafts
import server_mode
import taint
from ui.config.fields import Field
from ui.config.registry import EnvKeys, Section

FIELDS = (
    Field("mode", "Server mode", "choice", default=server_mode.FULL, env_var=server_mode.ENV,
          options=(server_mode.FULL, server_mode.READ_ONLY),
          help="Read-only removes every tool that reaches other people (send, vote, react, delete, reply). "
               "Give it to an agent that only has to read.",
          risk="In full mode the agent can send, vote, react, delete and reply on your behalf.", risk_when=(server_mode.FULL,)),
    Field("session_taint", "Session taint", "choice", default="block", env_var=taint.ENV, options=("block", "off"),
          help="Block sending to a chat other than the ones the session has read. The send policy file can also set it; "
               "this variable wins.",
          risk="Text read in one chat could make the agent carry its content to another chat.", risk_when=("off",)),
    Field("draft_ttl_hours", "Draft lifetime in hours", "float", default=float(drafts.DEFAULT_TTL_HOURS),
          env_var="WHATSAPP_MONITOR_DRAFT_TTL_HOURS", bounds=(0.01, float(drafts.MAX_TTL_HOURS)),
          help="How long a reply waiting for approval is kept."),
)

SECTION = Section(
    id="agent_safety", title="Agent safety", order=50, icon="lock", storage=EnvKeys(), restart="server", fields=FIELDS,
    description="What the MCP server lets an agent do. Read when the MCP server starts; restart it after a change.")
