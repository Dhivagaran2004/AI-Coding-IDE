from fastapi import APIRouter, HTTPException

from App.schema.terminal_schema import (
    TerminalCommandRequest,
)

from App.service.Terminal.terminal_service import (
    TerminalService,
)


router = APIRouter(
    prefix="/projects",
    tags=["Terminal"],
)


terminal_service = TerminalService()


@router.post("/{project_id}/terminal/execute")
def execute_terminal_command(
    project_id: int,
    request: TerminalCommandRequest,
):
    """
    Execute a shell command inside a project workspace.
    """

    try:

        result = terminal_service.execute_command(
            project_id=project_id,
            command=request.command,
        )

        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except FileNotFoundError as error:

        raise HTTPException(
            status_code=404,
            detail=str(error),
        )

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

# =========================================
# Stop Command
# =========================================

@router.post("/{project_id}/terminal/stop")
def stop_terminal_command(
    project_id: int,
):

    """
    Stop the currently running terminal command
    for a project.
    """

    try:

        result = (
            terminal_service.stop_command(
                project_id=project_id
            )
        )

        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
            "stopped": True,
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )
