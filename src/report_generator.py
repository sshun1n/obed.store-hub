import os
import logging
import re
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
    """Генератор производственных отчетов в формате PDF с умной группировкой."""
    
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
            "/usr/share/fonts/truetype/liberation"
        ]
        normal_names = ["arial.ttf", "LiberationSans-Regular.ttf"]
        bold_names = ["arialbd.ttf", "LiberationSans-Bold.ttf"]

        arial = next((os.path.join(p, n) for p in font_paths for n in normal_names if os.path.exists(os.path.join(p, n))), None)
        arial_bd = next((os.path.join(p, n) for p in font_paths for n in bold_names if os.path.exists(os.path.join(p, n))), None)

        if arial and arial_bd:
            try:
                pdfmetrics.registerFont(TTFont('Arial', arial))
                pdfmetrics.registerFont(TTFont('Arial-Bold', arial_bd))
                self.font_name, self.font_name_bold = 'Arial', 'Arial-Bold'
            except: pass

    def _get_normalized_group_name(self, name: str) -> str:
        """Возвращает нормализованное имя группы для заголовка (например, Блинчики)."""
        if not name: return ""
        first_word = name.split()[0].lower()
        
        # Словарь маппинга для красивых заголовков
        mappings = {
            "блинчики": "Блинчики", "блины": "Блинчики", "блинчик": "Блинчики",
            "сэндвич": "Сэндвичи", "сэндвичи": "Сэндвичи", "сэндвич-ролл": "Сэндвичи",
            "салат": "Салаты", "салаты": "Салаты",
            "каша": "Каши", "каши": "Каши",
            "суп": "Супы", "супы": "Супы",
            "десерт": "Десерты", "десерты": "Десерты"
        }
        
        # Если слова нет в маппинге, просто возвращаем первое слово с большой буквы
        # и убираем окончания для сравнения (чтобы Блины и Блинчики считались одной группой)
        norm_key = re.sub(r'(чики|ы|и|чик|ые|ие)$', '', first_word)
        
        # Ищем по маппингу
        for key, val in mappings.items():
            if re.sub(r'(чики|ы|и|чик|ые|ие)$', '', key) == norm_key:
                return val
                
        return first_word.capitalize()

    def _create_formatted_name_cell(self, main_part: str, composition: str) -> Paragraph:
        """Создает ячейку с названием и мелким составом."""
        main_part = main_part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        composition = composition.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        html = f'<font name="{self.font_name}" size="10"><b>{main_part}</b></font>'
        if composition:
            html += f'<br/><font name="{self.font_name}" size="7" color="#666666">{composition}</font>'
        return Paragraph(html, ParagraphStyle('Dish', fontName=self.font_name, fontSize=10, leading=12))

    def _create_workshop_table(self, data: List[Dict[str, Any]], title: str, date: str) -> List[Flowable]:
        """Создает таблицу с поддержкой промежуточных итогов."""
        elements = []
        h_style_bold = ParagraphStyle('Title', fontName=self.font_name_bold, fontSize=16, textColor=colors.HexColor('#1a3353'))
        d_style = ParagraphStyle('Date', fontName=self.font_name, fontSize=12, alignment=TA_RIGHT)
        
        header_table = Table([[Paragraph(title, h_style_bold), Paragraph(f"Дата доставки: {date}", d_style)]], colWidths=[110*mm, 90*mm])
        header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('BOTTOMPADDING', (0,0), (-1,-1), 6)]))
        elements.append(header_table)

        # Подготовка данных с группировкой
        t_data = [[
            Paragraph("№", ParagraphStyle('th', fontName=self.font_name_bold, fontSize=10, textColor=colors.white, alignment=TA_CENTER)),
            Paragraph("Наименование", ParagraphStyle('th', fontName=self.font_name_bold, fontSize=10, textColor=colors.white)),
            Paragraph("Шт.", ParagraphStyle('th', fontName=self.font_name_bold, fontSize=10, textColor=colors.white, alignment=TA_CENTER))
        ]]

        # Сначала сгруппируем товары в коде
        item_groups = []
        current_group = []
        current_group_name = None

        for item in data:
            g_name = self._get_normalized_group_name(item['main_name'])
            if g_name != current_group_name:
                if current_group: item_groups.append((current_group_name, current_group))
                current_group = [item]
                current_group_name = g_name
            else:
                current_group.append(item)
        if current_group: item_groups.append((current_group_name, current_group))

        # Формируем строки таблицы
        global_idx = 1
        sub_total_rows = [] # Индексы строк для стилизации (итоги)
        group_header_rows = [] # Индексы строк для стилизации (заголовки групп)

        for g_name, items in item_groups:
            use_grouping = len(items) >= 3
            
            if use_grouping:
                # Заголовок группы
                group_header_rows.append(len(t_data))
                t_data.append(["", Paragraph(f"<b>{g_name.upper()}</b>", ParagraphStyle('gh', fontName=self.font_name_bold, fontSize=10)), ""])
                
                group_sum = 0
                for item in items:
                    t_data.append([global_idx, self._create_formatted_name_cell(item['main_name'], item['composition']), item['quantity']])
                    group_sum += item['quantity']
                    global_idx += 1
                
                # Итог группы
                sub_total_rows.append(len(t_data))
                t_data.append([
                    "", 
                    Paragraph(f"<b>Итого {g_name.lower()}:</b>", ParagraphStyle('gt', fontName=self.font_name_bold, fontSize=10, alignment=TA_RIGHT)), 
                    Paragraph(f"<b>{group_sum}</b>", ParagraphStyle('gt_qty', fontName=self.font_name_bold, fontSize=10, alignment=TA_CENTER))
                ])
            else:
                # Просто выводим товары
                for item in items:
                    t_data.append([global_idx, self._create_formatted_name_cell(item['main_name'], item['composition']), item['quantity']])
                    global_idx += 1

        # Общий итог
        total_all = sum(item['quantity'] for item in data)
        t_data.append(["", Paragraph("<b>ИТОГО ВСЕГО:</b>", ParagraphStyle('total', fontName=self.font_name_bold, fontSize=10, alignment=TA_RIGHT)), Paragraph(f"<b>{total_all}</b>", ParagraphStyle('total', fontName=self.font_name_bold, fontSize=10, alignment=TA_CENTER))])

        table = Table(t_data, colWidths=[8*mm, 177*mm, 15*mm], repeatRows=1)
        
        # Базовый стиль
        style_cmds = [
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a3353')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (2,-1), 'CENTER'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#e8f4f8')), # Итог всего
        ]

        # Добавляем стили для заголовков групп и промежуточных итогов
        for r_idx in group_header_rows:
            style_cmds.append(('BACKGROUND', (0, r_idx), (-1, r_idx), colors.HexColor('#f3f4f6')))
            style_cmds.append(('SPAN', (1, r_idx), (2, r_idx))) # Объединяем ячейки
        
        for r_idx in sub_total_rows:
            style_cmds.append(('BACKGROUND', (0, r_idx), (-1, r_idx), colors.HexColor('#f9fafb')))
            style_cmds.append(('TEXTCOLOR', (2, r_idx), (2, r_idx), colors.HexColor('#1a3353')))

        table.setStyle(TableStyle(style_cmds))
        elements.append(table)
        return elements

    def generate_pdf(self, grouped_data: Dict[str, List[Dict[str, Any]]], order_date: str, filename: str) -> str:
        """Генерирует многостраничный PDF отчет."""
        path = os.path.join(self.output_dir, filename)
        doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=5*mm, rightMargin=5*mm, topMargin=5*mm, bottomMargin=5*mm)
        story = []
        categories = sorted(grouped_data.keys())
        for i, cat in enumerate(categories):
            story.extend(self._create_workshop_table(grouped_data[cat], cat.upper(), order_date))
            if i < len(categories) - 1: story.append(PageBreak())
        doc.build(story)
        return path
