#!/usr/bin/env python3
"""
SlidesBot: A Multi-Agent Hierarchical RAG System for CS100 Course Q&A

OVERVIEW:
This script implements an intelligent teaching assistant system that uses a multi-agent
architecture to answer student questions about C/C++ programming concepts based on
course lecture slides.

METHODOLOGY:
The system employs a hierarchical Retrieval-Augmented Generation (RAG) approach with
multiple AI agents working collaboratively:

1. LECTURE SUMMARIZATION PHASE:
   - Automatically processes all 29 lecture slides (0-10: C, 11-28: C++)
   - Generates structured summaries with keywords and brief descriptions
   - Stores summaries in JSON format for efficient retrieval

2. MULTI-AGENT ARCHITECTURE:
   
   a) Coordinator Agent (SlidesBot):
      - Analyzes student questions to identify required information
      - Strategically selects relevant lectures to query
      - Formulates specific questions for specialist agents
      - Synthesizes responses from multiple sources
      - Iteratively refines understanding through follow-up questions
   
   b) Specialist Agents (LectureBots):
      - Each agent is an expert on exactly one lecture's content
      - Answers questions based STRICTLY on their assigned lecture
      - Uses low temperature (0.3) and reasoning models for accuracy
      - Prevented from using external knowledge to ensure course alignment

3. ITERATIVE REASONING PROCESS:
   - Coordinator identifies which lectures likely contain relevant information
   - Asks targeted questions to multiple specialist agents in parallel
   - Evaluates gathered information and determines if more details are needed
   - Continues iterating until confident in providing a comprehensive answer
   - Synthesizes multi-lecture information into coherent final response

4. ADVANTAGES OVER TRADITIONAL RAG:
   - Active vs passive retrieval: AI decides what information it needs
   - Context-aware: Understands course progression and concept dependencies  
   - Iterative refinement: Can ask follow-up questions based on initial answers
   - Intelligent synthesis: Combines information from multiple sources meaningfully
   - No embedding/vector similarity required: Uses structured summaries and AI reasoning

USAGE:
    python bot.py -c my_llm_config.json --question "How do I pass arrays to functions in C?"
    python bot.py --max-iterations 10 --question "Explain object-oriented programming concepts"
    python bot.py  # Interactive mode (not yet implemented)

    Refer to LLM.init_from_config_file for config file format.

This system represents a sophisticated approach to educational AI, combining multi-agent
systems, agentic RAG, and iterative reasoning to provide accurate, course-aligned answers.
"""

import sys
from openai import OpenAI
import json
from pathlib import Path
from typing import List, Dict, Optional
import os
import re
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed


def parse_arguments():
    """
    bot.py [-c|--config-llm <config_file_path>] [--max-iterations N] [-q|--question "Your question here"]
    If --config-llm is provided, initializes LLM from the specified config file.
    If --max-iterations is not provided, defaults to 8.
    If --question is not provided, enters interactive mode.
    """
    parser = argparse.ArgumentParser(description="SlidesBot Command Line Interface")
    parser.add_argument('-c', '--config-llm', type=str, help='Path to LLM configuration JSON file')
    parser.add_argument('--max-iterations', type=int, default=8, help='Maximum iterations for the bot to reach a final answer (default: 8)')
    parser.add_argument('-q', '--question', type=str, help='The question to ask the SlidesBot. If not provided, enters interactive mode.')
    return parser.parse_args()


class LLM:
    client: OpenAI
    basic_model: str
    reasoner_model: Optional[str]

    def __init__(self, always_reason: bool = False):
        self.always_reason = always_reason
        if not self.reasoner_model:
            print("Warning: reasoner_model not set. Will fall back to basic_model for reasoning.", file=sys.stderr)

    def invoke(self, messages: List[Dict[str, str]], **more_args) -> str:
        if "model" not in more_args:
            more_args["model"] = LLM.reasoner_model if self.always_reason else LLM.basic_model
        if not more_args["model"]:
            more_args["model"] = LLM.basic_model
        more_args["messages"] = messages
        more_args["stream"] = False
        return LLM.client.chat.completions.create(**more_args).choices[0].message.content

    @staticmethod
    def init_gkxx_deepseek():
        """GKxx's personal DeepSeek configuration"""
        os.environ["all_proxy"] = os.environ["ALL_PROXY"] = ""
        try:
            LLM.client = OpenAI(api_key=os.environ["DS_APIKey_GKxxPersonal"], base_url="https://api.deepseek.com")
        except KeyError:
            raise RuntimeError("Environment variable DS_APIKey_GKxxPersonal not set. If you are not GKxx, please use --config-llm to provide your own configuration file.")
        LLM.basic_model = "deepseek-chat"
        LLM.reasoner_model = "deepseek-reasoner"

    @staticmethod
    def init_from_config_file(config_file_path: Path):
        """Initialize LLM from a JSON config file with keys: api_key, base_url, basic_model, reasoner_model (optional)"""
        with open(config_file_path, 'r') as f:
            config = json.load(f)
        os.environ["all_proxy"] = os.environ["ALL_PROXY"] = ""
        LLM.client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
        LLM.basic_model = config["basic_model"]
        LLM.reasoner_model = config.get("reasoner_model", None)


class LecturesMetadata:
    @staticmethod
    def is_valid_lecture_number(lec_no: int) -> bool:
        return 0 <= lec_no <= 28

    @staticmethod
    def get_valid_lecture_numbers() -> List[int]:
        return list(range(0, 29))

    @staticmethod
    def is_c_lecture(lec_no: int) -> bool:
        return 0 <= lec_no <= 10

    @staticmethod
    def is_cpp_lecture(lec_no: int) -> bool:
        return 11 <= lec_no <= 28

    @staticmethod
    def get_lecture_language(lec_no: int) -> str:
        assert LecturesMetadata.is_valid_lecture_number(lec_no)
        return 'C' if LecturesMetadata.is_c_lecture(lec_no) else 'C++'

    @staticmethod
    def get_lecture_dir(lec_no: int) -> Path:
        assert LecturesMetadata.is_valid_lecture_number(lec_no)
        return Path(__file__).parent / f"l{lec_no}"

    @staticmethod
    def contains_images(lec_no: int) -> bool:
        lec_dir = LecturesMetadata.get_lecture_dir(lec_no)
        return (lec_dir / "img").exists() or (lec_dir / "image").exists()

    @staticmethod
    def get_lecture_main_file(lec_no: int) -> Path:
        lec_dir = LecturesMetadata.get_lecture_dir(lec_no)
        if md_files := list(lec_dir.glob("*.md")):
            assert len(md_files) == 1, f"Multiple .md files found in {lec_dir}"
            return md_files[0]
        if tex_files := list(lec_dir.glob("*.tex")):
            assert len(tex_files) == 1, f"Multiple .tex files found in {lec_dir}"
            return tex_files[0]
        raise FileNotFoundError(f"No .md or .tex files found in {lec_dir}")


class Initializer:
    def __init__(self, readme_path: Path, summary_path: Path):
        self.readme_path = readme_path
        self.summary_path = summary_path
        self.summary = self._load_summary()
        self.titles = self._load_titles()
        self.remaining = self._get_remaining_lectures()

    def work(self, max_workers: int = 4):
        while self.remaining:
            self._summarize_all(workers=max_workers)
            self.remaining = self._get_remaining_lectures()
        print("All lectures summarized.")

    def _get_remaining_lectures(self) -> List[int]:
        return [i for i in range(len(self.titles)) if str(i) not in self.summary or not self._is_complete(self.summary[str(i)])]

    @staticmethod
    def _is_complete(lec_info: dict) -> bool:
        return isinstance(lec_info.get("title"), str) and \
            isinstance(lec_info.get("keywords"), list) and \
            all(isinstance(v, str) for v in lec_info["keywords"]) and \
            isinstance(lec_info.get("brief"), str)

    def _load_summary(self) -> dict:
        try:
            with open(self.summary_path, 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            return {}

    def _save_summary(self):
        with open(self.summary_path, 'w') as file:
            json.dump(self.summary, file, indent=2, ensure_ascii=False)

    def _load_titles(self) -> List[str]:
        with open(self.readme_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return list(map(lambda m: m[1], re.findall(r"- Lecture (\d+): (.+)", content)))

    @staticmethod
    def _summarize_lecture(lec_no: int, max_attempts: int) -> str:
        Lec = LecturesMetadata

        with open(Lec.get_lecture_main_file(lec_no), 'r', encoding='utf-8') as file:
            content = file.read()

        system_prompt = """You are an expert assistant that creates concise summaries of programming course lecture slides. Your task is to extract the key concepts, topics, and learning objectives from lecture content and present them in a structured JSON format.

        Guidelines:
        - Focus on the main programming concepts and techniques covered
        - Extract important syntax, keywords, functions, and concepts as keywords
        - Create a brief summary that captures the essential topics and learning objectives
        - Keep the summary concise but comprehensive enough to understand the lecture's scope
        - Output must be valid JSON with exactly two fields: "keywords" (array of strings) and "brief" (string)
        - Do NOT include introductory phrases in the brief summary
        - Keywords should include programming concepts, syntax elements, function names, data types, etc."""

        user_prompt = f"""Analyze the following {Lec.get_lecture_language(lec_no)} lecture content from an introductory C/C++ programming course (Lecture {lec_no}) and extract:

        1. Keywords: Important programming concepts, syntax elements, functions, data types, and techniques covered
        2. Brief summary: A concise overview of the main topics and learning objectives

        Output as JSON with "keywords" array and "brief" string. Example format:

        ```json
        {{
            "keywords": ["keyword1", "keyword2", "..."],
            "brief": "A concise summary of the lecture content."
        }}
        ```

        Lecture content:

        {content}"""
        for _ in range(max_attempts):
            try:
                return json.loads(LLM().invoke(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"}))
            except:
                pass
        raise RuntimeError(f"Failed to summarize lecture {lec_no} after {max_attempts} attempts.")

    def _summarize_all(self, workers: int = 4):
        results = {i: None for i in self.remaining}
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(self._summarize_lecture, i, 3): i for i in self.remaining}
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        results[i] = future.result()
                    except Exception as e:
                        print(f"Lecture {i} summarization failed: {e}")
        except:
            pass
        finally:
            for i, res in results.items():
                if res and (keywords := res.get("keywords")) and (brief := res.get("brief")):
                    self.summary[str(i)] = {
                        "title": self.titles[i],
                        "keywords": keywords,
                        "brief": brief
                    }
            self._save_summary()


class LectureBot:
    def __init__(self, lec_no: int, title: str, brief: str):
        assert LecturesMetadata.is_valid_lecture_number(lec_no)
        self.lec_no = lec_no
        self.title = title
        self.brief = brief # Maybe unused
        with open(LecturesMetadata.get_lecture_main_file(self.lec_no), 'r', encoding='utf-8') as file:
            self.content = file.read()

    def answer(self, question: str) -> str:
        Lec = LecturesMetadata
        system_prompt = f"""You are an expert teaching assistant for an introductory C/C++ programming course. Your task is to answer student questions based STRICTLY AND EXCLUSIVELY on the content of Lecture {self.lec_no}: "{self.title}", which is a {Lec.get_lecture_language(self.lec_no)} lecture.

CRITICAL INSTRUCTIONS:
- Answer ONLY based on the provided lecture content
- Do NOT use your general programming knowledge or external information
- If the answer is not found in the lecture content, explicitly state "This topic is not covered in this lecture"
- Quote or reference specific parts of the lecture when possible
- Stay within the scope of what is actually taught in this specific lecture
- External links, especially to cppreference.com, may be present in the lecture content. If they are relevant and important, you can include them in your answer using standard Markdown link syntax "[]()". Do NOT fabricate or assume any links; only use those explicitly provided in the lecture.

Lecture content:

{self.content}"""

        user_prompt = f"""Give an detailed answer to the following question using ONLY the information provided in the lecture content. Do not supplement with external knowledge.

Question: {question}
"""
        if Lec.contains_images(self.lec_no):
            user_prompt += f"""\nNote: If you want to use the images from the lecture, just use the normal Markdown syntax "![]()" and add {Lec.get_lecture_dir(self.lec_no).absolute()} before the images' relative paths."""

        return LLM(always_reason=True).invoke(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )


class InternalStepsPrinter:
    def __init__(self):
        pass

    def asking_over_lectures(self, reasoning: str, questions: List[dict]):
        print(f"\n\033[1mAsking questions based on reasoning:\033[0m {reasoning}\n")
        json.dump(questions, sys.stdout, indent=2, ensure_ascii=False)
        print('-' * 20)

    def received_answers_over_lectures(self, questions: List[dict], answers: List[str]):
        print(f"\n\033[1mReceived answers from lectures:\033[0m\n")
        for q, a in zip(questions, answers):
            print(f"Question:\n\tOver Lecture {q['lecture_number']}\n\t{q['question']}\n\nAnswer: {a}\n")
            print('-' * 20)


class SlidesBot:
    def __init__(self, summary_path: Path, readme_path: Path):
        init = Initializer(readme_path=readme_path, summary_path=summary_path)
        init.work(max_workers=12)
        self.lecture_summaries = {int(lec_no): summary for lec_no, summary in init.summary.items()}
        assert all(lec_no in self.lecture_summaries for lec_no in LecturesMetadata.get_valid_lecture_numbers())

        self.llm = LLM(always_reason=True)
        self.lecture_bots : Dict[int, LectureBot] = {}
        self.internal_printer = InternalStepsPrinter()

    def _ask_lecture_bot(self, lec_no: int, question: str) -> str:
        if lec_no in self.lecture_summaries:
            lec_info = self.lecture_summaries[lec_no]
            if lec_no not in self.lecture_bots:
                self.lecture_bots[lec_no] = LectureBot(lec_no, lec_info["title"], lec_info["brief"])
            return self.lecture_bots[lec_no].answer(question)
        else:
            valid_numbers = LecturesMetadata.get_valid_lecture_numbers()
            return f"Error: {lec_no} is not a valid lecture number. Valid numbers are {valid_numbers[0]}-{valid_numbers[-1]}."

    def _process_lecture_questions(self, reasoning: str, questions: List[dict], max_workers: int) -> List[str]:
        self.internal_printer.asking_over_lectures(reasoning, questions)
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_question = {
                executor.submit(self._ask_lecture_bot, q["lecture_number"], q["question"]): (i, q)
                for i, q in enumerate(questions)
            }
            for future in as_completed(future_to_question):
                i, q = future_to_question[future]
                try:
                    answer = future.result()
                    results[i] = f"From lecture {q['lecture_number']}:\n{answer}\n\n"
                except Exception as e:
                    results[i] = f"Error: {e}\n\n"
        results = [results[i] for i in range(len(results))]
        self.internal_printer.received_answers_over_lectures(questions, results)
        return results

    def answer_question(self, question: str, max_iterations: int) -> str:
        # Let the main AI decide to ask LectureBots questions on specific lectures,
        # until it is confident to generate a final answer.
        
        # Create lecture summaries for context
        lecture_summaries = ""
        for lec_no in sorted(self.lecture_summaries.keys()):
            lec_info = self.lecture_summaries[lec_no]
            lecture_summaries += f"Lecture {lec_no}: {lec_info['title']}\n"
            lecture_summaries += f"  Keywords: {', '.join(lec_info['keywords'])}\n"
            lecture_summaries += f"  Brief: {lec_info['brief']}\n\n"
        
        system_prompt = f"""You are an intelligent teaching assistant coordinator for an introductory C/C++ programming course. Your role is to help students by strategically gathering information from specific lectures to answer their questions comprehensively.

AVAILABLE LECTURES (0-10 are C, 11-28 are C++):
{lecture_summaries}

YOUR PROCESS:
1. Analyze the student's question to understand what concepts/topics are involved
2. Identify which specific lectures likely contain relevant information
3. Ask targeted questions to those specific lectures to gather detailed information
4. Synthesize the gathered information into a comprehensive final answer

RESPONSE FORMAT:
You must respond in JSON format with one of two actions:

1. To gather more information:
{{
    "action": "ask_questions",
    "reasoning": "Brief explanation of why you're asking these questions",
    "questions": [
        {{"lecture_number": N, "question": "Specific question about this lecture's content"}},
        ...
    ]
}}

2. When ready to provide final answer:
{{
    "action": "final_answer", 
    "answer": "Your comprehensive answer based on the gathered information"
}}

GUIDELINES:
- Ask specific, targeted questions to relevant lectures
- Don't ask too many questions at once (3-5 max per iteration)
- Build upon previous answers to ask follow-up questions if needed
- Provide comprehensive final answers that synthesize information from multiple lectures when appropriate
- If a topic spans multiple lectures, gather information from all relevant ones
- Lecture slides may contain images and external links. Answers can include them using standard Markdown syntax "![]()" and "[]()" with appropriate paths/URLs. As a helpful teaching assistant, you can use these resources to enhance your explanations."""

        prompt = f"""Student Question: {question}

Please analyze this question and determine what information you need to gather from specific lectures to provide a comprehensive answer. Start by identifying which lectures are most likely to contain relevant information, then ask targeted questions to gather the details needed."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        for _ in range(max_iterations):
            response_str = self.llm.invoke(messages, response_format={"type": "json_object"})
            messages.append({"role": "assistant", "content": response_str})
            try:
                response = json.loads(response_str)
            except json.JSONDecodeError as e:
                messages.append({"role": "user", "content": f"The previous response was not valid JSON: {e}. Please make sure the response is in JSON format."})
                continue
            # We simply assume the response is valid hereafter
            if response["action"] == "ask_questions":
                results = self._process_lecture_questions(response["reasoning"], response["questions"], 8)
                answers = "Answers:\n\n" + "".join(results)
                
                continue_prompt = f"""Based on the answers above, do you have enough information to provide a comprehensive final answer to the student's question? 

If YES: Provide your final answer using the "final_answer" action.
If NO: Ask additional targeted questions to gather more specific information you need.

Remember to synthesize information from multiple lectures when relevant and provide practical examples or explanations that help the student understand the concepts.

Student's question was: {question}
"""
                messages.append({"role": "user", "content": answers + continue_prompt})
            elif response["action"] == "final_answer":
                return response["answer"]
            else:
                messages.append({"role": "user", "content": f"Unknown action '{response['action']}'. Please respond with either 'ask_questions' or 'final_answer'."})

        force_answer_prompt = """Maximum iterations reached. Please provide the best possible final answer based on the information gathered so far.
        Your response should still be in a valid JSON format with the action 'final_answer' and an 'answer' field. Example:

        ```json
        {
            "action": "final_answer",
            "answer": "Your final answer here."
        }
        ```
        """
        messages.append({"role": "user", "content": force_answer_prompt})
        for _ in range(3):
            try:
                return json.loads(self.llm.invoke(messages, response_format={"type": "json_object"}))["answer"]
            except:
                pass
        return "Failed to generate a final answer after maximum attempts."


def main():
    args = parse_arguments()
    if args.config_llm:
        LLM.init_from_config_file(Path(args.config_llm))
    else:
        LLM.init_gkxx_deepseek()
    bot = SlidesBot(summary_path=Path(__file__).parent / "summary.json",
                    readme_path=Path(__file__).parent / "README.md")
    if args.question is not None:
        answer = bot.answer_question(args.question, args.max_iterations)
        print(f"\n\033[1mFinal answer\033[0m:\n\n{answer}")
    else:
        print("Entering interactive mode. Type 'exit' to quit.")
        raise NotImplementedError("Interactive mode is not implemented yet.")


if __name__ == "__main__":
    main()
