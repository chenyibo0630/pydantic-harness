from main_agent.tools.bash import bash_execute
from main_agent.tools.file import read_file, list_dir, str_replace, write_file

TOOLS = [bash_execute, read_file, list_dir, str_replace, write_file]
