"""

"""
import json
import shutil
import tempfile

from ai_models_services import AWS_TEXTRACT_CLIENT, GPT4_CLIENT
from llm_response_validation import GPT4_RESPONSE_VALIDATION
from prompts import Prompts
from utils import IMAGE_SERVICE, JSON_ENCODER_DECODER


class Book:
    def __init__(self, file_name):
        book = JSON_ENCODER_DECODER.data_load(fname=file_name)[0]
        self.book_details = {
            "id": book["id"],
            "title": book["title"],
            "grade_subject_id": book["grade_subject_id"],
            "book_text": book["book_text"],
        }

    def add_chapter(self):
        book_chapters = JSON_ENCODER_DECODER.data_load(
            fname="input_data/book_library_bookchapter.json"
        )
        PROMPTS = Prompts(grade_subject=self.book_details["grade_subject_id"])

        for index, book_chapter in enumerate(book_chapters):
            if (
                index != 3
            ):  # the if else for testing purpose that control number of chapter to process
                pass
            else:
                print(f"book_chapter {book_chapter['id']} _ {book_chapter['title']}")
                chapter = BookChapter(
                    book_details=self.book_details,
                    book_chapter=book_chapter,
                    prompts=PROMPTS,
                )
                chapter.execute_extraction_process()


class BookChapter:

    """
    Class BookExercises is intended to extract book exercises by the following process:
    1. Retrieve book and book chapter details from the core backend.
    2. get exercises page numbers from chapter text and download these pages to local directory.
    3. Perform OCR on pages using GPT Vision to extract the questions in the intended format with detailed image descriptions.
    4. Extract the images from the page using AWS Layout OCR.
    5. Validate the images by their height and width.
    6. Upload validated images to AWS.
    7. using GPT4 Link the images to questions by matching the image to its description.
    8. Associate exercise questions with Student Learning Outcomes (SLOs) (LP_INPUTS).
    9. Solve these questions using GPT-4 and SLO content.
    10. Assign a marking scheme to the exercise using GPT-4.
    11. Validate question structure format and push the questions to the core database.
    """

    def __init__(self, book_details, book_chapter, prompts):
        self.book_details = book_details
        self.chapter_details = book_chapter
        self.PROMPTS = prompts
        self.chapter_exercises = None
        self.exercises_pages_temp_file = None
        self.extracted_exercises = []
        self.page_wise_figures = {}

    def execute_extraction_process(self):
        # check if book exercise exist or not
        chapter_text = self.chapter_details["chapter_text"]
        if chapter_text:
            self.chapter_exercises = chapter_text.get("exercises", None)
        else:
            print(
                f"Chapter id {self.chapter_details['id']} does not contain chapter text"
            )

        if self.chapter_exercises:
            self.download_exercises_pages()
            self.extract_questions_with_image_descriptions()
            self.extract_figures_from_pages()
            self.link_images_to_questions()
            self.solve_questions()
            self.assign_marking_scheme()
            print("Done")
            # self.validate_question_structure_and_push_to_database()
        else:
            print("Book Chapter text exit but has no exercise questions Found")

    def download_exercises_pages(self):
        """ using chapter_exercises presented inside chapter text """
        print("Download_exercises_pages started")
        unique_pages = set()
        for exercise in self.chapter_exercises:
            unique_pages.add(exercise.get("page_number"))

        unique_pages = list(unique_pages)

        # Create a temporary directory to store images
        temp_dir = tempfile.mkdtemp()

        # list of dicts {book_page_no: url}
        book_page_wise_url = []

        # book page url saved in book_text, for unique_pages create a dict from of book_page number and its url
        for item in self.book_details["book_text"]:
            if "book_page_no" in item and "pdf_page_image_url" in item:
                if item["book_page_no"] in unique_pages:
                    temp_file = IMAGE_SERVICE.download_image(
                        item["pdf_page_image_url"], temp_dir
                    )
                    book_page_wise_url.append({item["book_page_no"]: temp_file})
            else:
                print("book_page_no or pdf_page_image_url not found in book text")

        # Save the temporary directory path for later cleanup
        self.temp_dir = temp_dir

        # Save the dictionary of page numbers and temporary file paths
        self.exercises_pages_temp_file = {
            page_no: temp_file
            for page_dict in book_page_wise_url
            for page_no, temp_file in page_dict.items()
        }

        JSON_ENCODER_DECODER.data_write(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_exercise_pages",
            data=self.exercises_pages_temp_file,
        )

    def extract_questions_with_image_descriptions(self):
        print("extract_questions_with_image_descriptions started")

        # Perform OCR on pages using GPT Vision to extract questions
        # Extract detailed image descriptions if they exist
        prompt_text = self.PROMPTS.get_extract_exercises_prompt()
        constructed_response_prompt = (
            self.PROMPTS.get_constructed_response_exercises_prompt()
        )

        response = GPT4_CLIENT.get_gpt_4_vision(
            prompt_text=prompt_text,
            image_source="local_file",
            exercises_pages_temp_file=self.exercises_pages_temp_file,
        )
        print("response", response)

        constructed_questions = GPT4_CLIENT.get_gpt_4_vision(
            prompt_text=constructed_response_prompt,
            image_source="local_file",
            exercises_pages_temp_file=self.exercises_pages_temp_file,
        )
        print("constructed_questions", constructed_questions)

        # validate responses
        self.extracted_exercises = (
            self.extracted_exercises
            + GPT4_RESPONSE_VALIDATION.get_questions_array_from_response(
                response=response
            )
        )
        self.extracted_exercises = (
            self.extracted_exercises
            + GPT4_RESPONSE_VALIDATION.get_questions_array_from_response(
                response=constructed_questions
            )
        )

        JSON_ENCODER_DECODER.data_write(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_questions",
            data=self.extracted_exercises,
        )

    def extract_figures_from_pages(self):
        print("Extract_figures_from_pages started")

        """ For image-based questions,
        1. extract images layout from the page using AWS Layout OCR
        2. crop images using openCV2,
        3. upload sub-images to AWS
        """
        # this part used when want to partial test functions
        self.exercises_pages_temp_file = JSON_ENCODER_DECODER.data_load(fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_exercise_pages.json")
        self.extracted_exercises = JSON_ENCODER_DECODER.data_load(fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_questions")

        page_figures = {}
        pages_has_images = set()
        for question in self.extracted_exercises:
            if question["image_based_question"]:
                pages_has_images.add(int(question["question_page_number"]))

        print("pages has images", pages_has_images)

        # process image by image
        for page_number in list(pages_has_images):

            figures_links = []
            page_file_path = self.exercises_pages_temp_file[str(page_number)]
            # get list of validated images coordinates.
            coordinates_list = AWS_TEXTRACT_CLIENT.get_figures_coordinates(
                img_path=page_file_path
            )
            if coordinates_list:
                print(
                    "number of figures found",
                    len(coordinates_list),
                    f"at page {page_number}",
                )
                # Loop through the coordinates and crop the image
                for index, coordinates in enumerate(coordinates_list, start=1):
                    cropped_image = IMAGE_SERVICE.crop_image_opencv(
                        image_path=page_file_path, coordinates=coordinates
                    )

                    # upload image to aws
                    image_name = (
                        f"chapter_id_{self.chapter_details['id']}_{page_number}.{index}"
                    )
                    link = AWS_TEXTRACT_CLIENT.upload_image_to_aws(
                        image=cropped_image, image_key=image_name
                    )
                    print("link", link)
                    figures_links.append(link)

                page_figures[page_number] = figures_links
            else:
                print(f"no figures found at page {page_number}")

        self.page_wise_figures = page_figures
        JSON_ENCODER_DECODER.data_write(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_page_wise_figures",
            data=self.page_wise_figures,
        )
        # Delete the temporary file once done
        shutil.rmtree(self.temp_dir)

    def link_images_to_questions(self):
        """ Link the images to questions by matching the image to its description"""
        print("link_images_to_questions started")

        # this part used when want to partial test functions
        self.extracted_exercises = JSON_ENCODER_DECODER.data_load(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_questions"
        )
        self.page_wise_figures = JSON_ENCODER_DECODER.data_load(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_page_wise_figures"
        )

        for question in self.extracted_exercises:
            if question["image_based_question"]:
                question_page_number = question["question_page_number"]
                page_figures = self.page_wise_figures.get(
                    str(question_page_number), None
                )
                if page_figures:
                    images_urls = page_figures
                    prompt = self.PROMPTS.prompt_link_image_to_questions(
                        question=question, images_links=images_urls
                    )
                    # print("link image prompt", prompt)

                    question_images_urls = GPT4_CLIENT.get_gpt_4_vision(
                        prompt_text=prompt,
                        image_source="link",
                        images_links=images_urls,
                    )

                    urls_list = GPT4_RESPONSE_VALIDATION.get_images_urls(
                        response=question_images_urls
                    )

                    if urls_list:
                        question["question_image_url"] = urls_list[0]
                    else:
                        question["question_image_url"] = question_images_urls

        JSON_ENCODER_DECODER.data_write(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_Linked_images",
            data=self.extracted_exercises,
        )

    def solve_questions(self):
        """ Divide all the questions into chapter content"""
        print("solve_questions started")

        # this part used when want to partial test functions
        # self.extracted_exercises = JSON_ENCODER_DECODER.data_load(
        #     fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_Linked_images"
        # )

        chapter_text = self.chapter_details["chapter_text"]
        prompt_text = self.PROMPTS.solve_questions_prompt(
            questions=self.extracted_exercises,
            chapter_content={
                "SLOs": chapter_text["slos"],
                "topics_content": chapter_text["topics"],
            },
        )

        json_response = GPT4_CLIENT.get_gpt_4_1106_preview(prompt=prompt_text)
        json_response = json.loads(json_response)
        self.extracted_exercises = json_response.get("questions", [])

        JSON_ENCODER_DECODER.data_write(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_solved",
            data=self.extracted_exercises,
        )

    def assign_marking_scheme(self):
        """ Assign a marking scheme to the exercise using GPT-4, T/F and MCQ will have score of 1. SAQ, LAQ and
        constructed response will have marking-scheme.
        """
        print("Assign_marking_scheme started")
        # this part used when want to partial test functions
        self.extracted_exercises = JSON_ENCODER_DECODER.data_load(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_solved"
        )

        saq = []
        laq = []
        constructed = []
        rest_questions = []
        for question in self.extracted_exercises:
            q_type = question["question_type"]
            if q_type == "short-answer":
                saq.append(question)
            elif q_type == "long-answer":
                laq.append(question)
            elif q_type == "constructed-response":
                constructed.append(question)
            else:
                rest_questions.append(question)

        # assign marking-scheme for SAQ
        marking_scheme_prompt = self.PROMPTS.marking_scheme(
            question_type="short_answer", question_array=saq, question_source='exercise'
        )
        json_response = GPT4_CLIENT.get_gpt_4_1106_preview(prompt=marking_scheme_prompt)
        json_response = json.loads(json_response)
        self.extracted_exercises = json_response.get("questions", [])

        # assign marking-scheme for LAQ
        marking_scheme_prompt = self.PROMPTS.marking_scheme(
            question_type="long_answer", question_array=laq, question_source='exercise'
        )
        json_response = GPT4_CLIENT.get_gpt_4_1106_preview(prompt=marking_scheme_prompt)
        json_response = json.loads(json_response)
        self.extracted_exercises = self.extracted_exercises + json_response.get(
            "questions", []
        )

        # assign marking-scheme for construct
        prompt_text: str = self.PROMPTS.constructed_marking_scheme(
            question_array=constructed,
        )
        json_response = GPT4_CLIENT.get_gpt_4_1106_preview(prompt=prompt_text)
        json_response = json.loads(json_response)
        self.extracted_exercises = self.extracted_exercises + json_response.get(
            "questions", []
        )

        for other_question in rest_questions:
            other_question["marking_scheme"] = None
            other_question["score"] = 1

        self.extracted_exercises = self.extracted_exercises + rest_questions
        JSON_ENCODER_DECODER.data_write(
            fname=f"output_progress/{self.book_details['id']}_{self.chapter_details['id']}_marking_scheme",
            data=self.extracted_exercises,
        )

    def validate_question_structure_and_push_to_database(self):
        # Validate question structure format and push questions to the core database
        pass


book = Book(file_name="input_data/book.json")
book.add_chapter()
