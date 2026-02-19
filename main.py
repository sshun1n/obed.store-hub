import argparse
import logging
import sys
from datetime import datetime
from typing import List, Tuple
from dotenv import load_dotenv

# Import services and custom exceptions
from service import (
    AppConfig, GoogleSheetClient, NomenclatureService,
    ConfigurationError, SheetAPIError, ServiceError
)
from report_generator import ReportGenerator

# Configure basic logging for the CLI
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Main")

def parse_arguments() -> argparse.Namespace:
    # Defines and parses command-line arguments for the script.
    parser = argparse.ArgumentParser(description="Service for aggregating orders from Google Sheets.")
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true", 
        help="Print the aggregated nomenclature table to the console."
    )
    parser.add_argument(
        "-d", "--date",
        type=str,
        default=datetime.now().strftime("%d.%m.%Y"),
        help="Order date for the report (format: DD.MM.YYYY). Defaults to today."
    )
    return parser.parse_args()

def main() -> None:
    # Main execution function for the command-line interface.
    load_dotenv()
    args = parse_arguments()

    try:
        # Initialize configuration and all necessary services.
        config = AppConfig()
        config.validate()
        
        google_client = GoogleSheetClient(config)
        service = NomenclatureService(google_client)
 
        # Retrieve aggregated data from the service.
        logger.info("Fetching data from Google Sheets...")
        nomenclature: List[Tuple[str, int]] = service.get_aggregated_nomenclature()

        if not nomenclature:
            logger.warning("No data found for the specified criteria. Exiting.")
            return

        # If the --verbose flag is used, print a formatted table to the console.
        if args.verbose:
            print("\n" + "-" * 60)
            print(f"{'НАИМЕНОВАНИЕ':<45} | {'КОЛ-ВО'}")
            print("-" * 60)
            for name, quantity in nomenclature:
                # Truncate long names for better console display.
                display_name = (name[:42] + '..') if len(name) > 42 else name
                print(f"{display_name:<45} | {quantity}")
            print("-" * 60 + "\n")

        # Generate the PDF report.
        logger.info(f"Generating PDF report for date: {args.date}...")
        report_gen = ReportGenerator(output_dir="reports")
        pdf_path: str = report_gen.generate_pdf(
            nomenclature=nomenclature,
            order_date=args.date  # Use the date from arguments
        )
        
        print(f"✅ Успешно! Отчет сохранен: {pdf_path}")

    # Catch specific, known errors for user-friendly feedback.
    except ConfigurationError as e:
        logger.critical(f"Ошибка конфигурации: {e}")
        logger.critical("Пожалуйста, проверьте переменные окружения в вашем .env файле.")
        sys.exit(1)
    except SheetAPIError as e:
        logger.critical(f"Ошибка Google Sheets API: {e}")
        logger.critical("Проверьте подключение к сети и права доступа сервисного аккаунта.")
        sys.exit(1)
    # Catch any other unexpected errors.
    except Exception as e:
        logger.critical(f"Произошла непредвиденная ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
