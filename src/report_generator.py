import os
import logging
from datetime import datetime
from typing import List, Tuple, Optional, Any, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, 
    Spacer, PageBreak, Flowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

logger = logging.getLogger(__name__)

class ReportGenerator:
    """Генератор производственных отчетов в формате PDF."""
    
    def __init__(self, output_dir: str = "reports") -> None:
        self.output_dir = output_dir
        self.font_name = 'Helvetica'
        self.font_name_bold = 'Helvetica-Bold'
        self._ensure_output_dir()
        self._register_fonts()
        
    def _ensure_output_dir(self) -> None:
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def _register_fonts(self) -> None:
        """Регистрация шрифтов для поддержки кириллицы (Arial или Liberation)."""
        font_paths = [
            "C:\\Windows\\Fonts", 
            "/usr/share/fonts/truetype/msttcorefonts",
            "/usr/share/fonts/truetype/liberation" # Для Linux/Docker
        ]
        
        # Список возможных имен файлов для обычного и жирного шрифта
        normal_names = ["arial.ttf", "LiberationSans-Regular.ttf"]
        bold_names = ["arialbd.ttf", "LiberationSans-Bold.ttf"]

        arial = None
        for path in font_paths:
            for name in normal_names:
                full_path = os.path.join(path, name)
                if os.path.exists(full_path):
                    arial = full_path
                    break
            if arial: break

        arial_bd = None
        for path in font_paths:
            for name in bold_names:
                full_path = os.path.join(path, name)
                if os.path.exists(full_path):
                    arial_bd = full_path
                    break
            if arial_bd: break

        if arial and arial_bd:
            try:
                pdfmetrics.registerFont(TTFont('Arial', arial))
                pdfmetrics.registerFont(TTFont('Arial-Bold', arial_bd))
                self.font_name, self.font_name_bold = 'Arial', 'Arial-Bold'
                logger.info(f"Шрифты зарегистрированы: {os.path.basename(arial)}")
            except Exception as e:
                logger.error(f"Ошибка регистрации шрифтов: {e}")

    def _create_formatted_name_cell(self, main_part: str, composition: str) -> Paragraph:
        """Создает ячейку с названием и мелким составом."""
        main_part = main_part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        composition = composition.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        html = f'<font name="{self.font_name}" size="10"><b>{main_part}</b></font>'
        if composition:
            html += f'<br/><font name="{self.font_name}" size="7" color="#666666">{composition}</font>'
        
        # Leading 12 обеспечивает комфортный интервал при шрифте 10
        style = ParagraphStyle('Dish', fontName=self.font_name, fontSize=10, leading=12)
        return Paragraph(html, style)

    def _create_workshop_table(self, data: List[Dict[str, Any]], title: str, date: str) -> List[Flowable]:
        """Создает таблицу для одной категории."""
        elements = []
        
        # Стили для заголовка
        h_style = ParagraphStyle('Title', fontName=self.font_name_bold, fontSize=16, textColor=colors.HexColor('#1a3353'))
        d_style = ParagraphStyle('Date', fontName=self.font_name, fontSize=12, alignment=TA_RIGHT)
        
        # Шапка страницы (Название категории и дата)
        # Ширина 110 + 90 = 200мм (при полях по 5мм остается ровно 200мм)
        header_table = Table([[Paragraph(title, h_style), Paragraph(f"Дата доставки: {date}", d_style)]], colWidths=[110*mm, 90*mm])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6)
        ]))
        elements.append(header_table)

        # Данные таблицы
        # № (8мм) + Название (177мм) + Шт (15мм) = 200мм
        t_data = [[
            Paragraph("№", ParagraphStyle('th', fontName=self.font_name_bold, fontSize=10, textColor=colors.white, alignment=TA_CENTER)),
            Paragraph("Наименование", ParagraphStyle('th', fontName=self.font_name_bold, fontSize=10, textColor=colors.white)),
            Paragraph("Шт.", ParagraphStyle('th', fontName=self.font_name_bold, fontSize=10, textColor=colors.white, alignment=TA_CENTER))
        ]]
        
        total_qty = 0
        for i, item in enumerate(data, 1):
            t_data.append([
                i, 
                self._create_formatted_name_cell(item['main_name'], item['composition']), 
                item['quantity']
            ])
            total_qty += item['quantity']

        # Итоговая строка
        t_data.append([
            "", 
            Paragraph("<b>ИТОГО:</b>", ParagraphStyle('total', fontName=self.font_name_bold, fontSize=10, alignment=TA_RIGHT)),
            Paragraph(f"<b>{total_qty}</b>", ParagraphStyle('total', fontName=self.font_name_bold, fontSize=10, alignment=TA_CENTER))
        ])

        table = Table(t_data, colWidths=[8*mm, 177*mm, 15*mm], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a3353')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (2,-1), 'CENTER'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            # Цвет для итоговой строки
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#e8f4f8')),
        ]))
        elements.append(table)
        return elements

    def generate_pdf(self, grouped_data: Dict[str, List[Dict[str, Any]]], order_date: str, filename: str) -> str:
        """Генерирует многостраничный PDF отчет."""
        path = os.path.join(self.output_dir, filename)
        
        # Устанавливаем четкие поля по 5мм
        doc = SimpleDocTemplate(
            path, 
            pagesize=A4,
            leftMargin=5*mm,
            rightMargin=5*mm,
            topMargin=5*mm,
            bottomMargin=5*mm
        )
        
        story = []
        categories = sorted(grouped_data.keys())
        for i, cat in enumerate(categories):
            story.extend(self._create_workshop_table(grouped_data[cat], cat.upper(), order_date))
            if i < len(categories) - 1:
                story.append(PageBreak())
        
        doc.build(story)
        return path
