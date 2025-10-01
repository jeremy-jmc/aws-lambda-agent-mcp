from pydantic import BaseModel, Field
from typing import Optional


class Link(BaseModel):
    url: str = Field(description="The URL of the link.")
    title: Optional[str] = Field(default=None, description="The title of the link, else it will be generated from the URL.")
    # description: Optional[str] = Field(default=None, description="A brief description of the link's content or purpose.")


class AgentResponse(BaseModel):
    main_topic: str = Field(description="Main topic of the message")
    intent: str = Field(description="Intent of the message")
    analysis: str = Field(description="Your detailed analysis of the message, including actionable insights and recommendations.")
    processing_steps: list[str] = Field(description="A numbered list of steps taken to analyze the message and generate the response, including tool usage and reasoning.")
    tasks: list[str] = Field(description="A numbered list of specific, actionable steps to address the message's content.")
    documentation: list[Link] = Field(description="Links to documentation, examples, and resources that support your analysis and recommendations.")
    summary: str = Field(description="A concise response summarizing findings, providing links, resources, and actionable next steps in a clear and pragmatic tone.")


class Participant(BaseModel):
    name: str = Field(description="Name of the person.")
    role: Optional[str] = Field(default=None, description="Role of the person in the channel. If not provided, this field is None.")


class MessageParticipants(BaseModel):
    cot: str = Field(description="Chain of thought explaining how the receivers were determined.")
    sender: Participant = Field(description="Person who sent the message. It should be include their name and role in the channel.")
    receivers: list[Participant] = Field(description="List of people (name and role) who should pay attention to the message. They can be refered by only their names or with a tag using '@'")

