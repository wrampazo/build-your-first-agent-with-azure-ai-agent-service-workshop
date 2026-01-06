import asyncio
import logging

from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import (
    Agent,
    AgentThread,
    AsyncFunctionTool,
    AsyncToolSet,
    CodeInterpreterTool,
    FileSearchTool,
)
from azure.identity.aio import DefaultAzureCredential


from config import Config
from sales_data import SalesData
from stream_event_handler import StreamEventHandler
from terminal_colors import TerminalColors as tc
from utilities import Utilities

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

INSTRUCTIONS_FILE = None


toolset = AsyncToolSet()
utilities = Utilities()
sales_data = SalesData(utilities)


agents_client = AgentsClient(
    credential=DefaultAzureCredential(),
    endpoint=Config.PROJECT_ENDPOINT,
)

functions = AsyncFunctionTool(
    {
        sales_data.async_fetch_sales_data_using_sqlite_query,
    }
)

INSTRUCTIONS_FILE = "instructions/function_calling.txt"
INSTRUCTIONS_FILE = "instructions/file_search.txt"
INSTRUCTIONS_FILE = "instructions/code_interpreter.txt"
INSTRUCTIONS_FILE = "instructions/code_interpreter_multilingual.txt"


async def add_agent_tools():
    """Add tools for the agent."""
    font_file_info = None

    # Add the functions tool
    toolset.add(functions)

    # Add the tents data sheet to a new vector data store
    vector_store = await utilities.create_vector_store(
        agents_client,
        files=[Config.TENTS_DATA_SHEET_FILE],
        vector_store_name="Contoso Product Information Vector Store",
    )
    file_search_tool = FileSearchTool(vector_store_ids=[vector_store.id])
    toolset.add(file_search_tool)

    # Add the code interpreter tool
    code_interpreter = CodeInterpreterTool()
    toolset.add(code_interpreter)

    # Add multilingual support to the code interpreter
    font_file_info = await utilities.upload_file(agents_client, utilities.shared_files_path / Config.FONTS_ZIP)
    code_interpreter.add_file(file_id=font_file_info.id)

    return font_file_info


async def initialize() -> tuple[Agent | None, AgentThread | None]:
    """Initialize the agent with the sales data schema and instructions."""

    if not INSTRUCTIONS_FILE:
        return None, None

    if not Config.API_DEPLOYMENT_NAME:
        logger.error("MODEL_DEPLOYMENT_NAME environment variable is not set")
        return None, None

    font_file_info = await add_agent_tools()

    await sales_data.connect()
    database_schema_string = await sales_data.get_database_info()

    try:
        instructions = utilities.load_instructions(INSTRUCTIONS_FILE)
        # Replace the placeholder with the database schema string
        instructions = instructions.replace(
            "{database_schema_string}", database_schema_string)

        if font_file_info:
            # Replace the placeholder with the font file ID
            instructions = instructions.replace(
                "{font_file_id}", font_file_info.id)

        print("Creating agent...")
        agent = await agents_client.create_agent(
            model=Config.API_DEPLOYMENT_NAME,
            name=Config.AGENT_NAME,
            instructions=instructions,
            toolset=toolset,
            temperature=Config.TEMPERATURE,
        )
        print(f"Created agent, ID: {agent.id}")

        agents_client.enable_auto_function_calls(tools=toolset)
        print("Enabled auto function calls.")

        print("Creating thread...")
        thread = await agents_client.threads.create()
        print(f"Created thread, ID: {thread.id}")

        return agent, thread

    except Exception as e:
        logger.error("An error occurred initializing the agent: %s", str(e))
        logger.error("Please ensure you've enabled an instructions file.")
        return None, None


async def cleanup(agent: Agent, thread: AgentThread) -> None:
    """Cleanup the resources."""
    existing_files = await agents_client.files.list()
    for f in existing_files.data:
        await agents_client.files.delete(f.id)
    await agents_client.threads.delete(thread.id)
    await agents_client.delete_agent(agent.id)
    await sales_data.close()


async def post_message(thread_id: str, content: str, agent: Agent, thread: AgentThread) -> None:
    """Post a message to the Foundry Agent Service."""
    try:
        await agents_client.messages.create(
            thread_id=thread_id,
            role="user",
            content=content,
        )

        async with await agents_client.runs.stream(
            thread_id=thread.id,
            agent_id=agent.id,
            event_handler=StreamEventHandler(
                functions=functions, agent_client=agents_client, utilities=utilities),
            max_completion_tokens=Config.MAX_COMPLETION_TOKENS,
            max_prompt_tokens=Config.MAX_PROMPT_TOKENS,
            temperature=Config.TEMPERATURE,
            top_p=Config.TOP_P,
            # instructions=agent.instructions,
        ) as stream:
            await stream.until_done()

    except Exception as e:
        utilities.log_msg_purple(
            f"An error occurred posting the message: {e!s}")


async def main() -> None:
    """
    Example questions: Sales by region, top-selling products, total shipping costs by region, show as a pie chart.
    """
    async with agents_client:
        agent, thread = await initialize()
        if not agent or not thread:
            print(f"{tc.BG_BRIGHT_RED}Initialization failed. Ensure you have uncommented the instructions file for the lab.{tc.RESET}")
            print("Exiting...")
            return

        cmd = None

        while True:
            prompt = input(
                f"\n\n{tc.GREEN}Enter your query (type exit or save to finish): {tc.RESET}").strip()
            if not prompt:
                continue

            cmd = prompt.lower()
            if cmd in {"exit", "save"}:
                break

            await post_message(agent=agent, thread_id=thread.id, content=prompt, thread=thread)

        if cmd == "save":
            print("The agent has not been deleted, so you can continue experimenting with it in the Azure AI Foundry.")
            print(
                f"Navigate to https://ai.azure.com, select your project, then playgrounds, agents playgound, then select agent id: {agent.id}"
            )
        else:
            await cleanup(agent, thread)
            print("The agent resources have been cleaned up.")


if __name__ == "__main__":
    print("Starting async program...")
    asyncio.run(main())
    print("Program finished.")
