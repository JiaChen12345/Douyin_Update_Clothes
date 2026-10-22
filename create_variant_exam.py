from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pathlib import Path

OUT = Path(r"E:\Try-onApp\douyin-draft-automation\2025年广东春季高考数学变式自测卷.docx")

def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr(); shd = OxmlElement('w:shd'); shd.set(qn('w:fill'), fill); tcPr.append(shd)

def set_cell_margins(cell, top=90, start=120, bottom=90, end=120):
    tcPr = cell._tc.get_or_add_tcPr(); tcMar = tcPr.first_child_found_in('w:tcMar')
    if tcMar is None: tcMar = OxmlElement('w:tcMar'); tcPr.append(tcMar)
    for m, v in [('top', top), ('start', start), ('bottom', bottom), ('end', end)]:
        node = tcMar.find(qn('w:'+m))
        if node is None: node = OxmlElement('w:'+m); tcMar.append(node)
        node.set(qn('w:w'), str(v)); node.set(qn('w:type'), 'dxa')

def add_question(doc, text, options=None):
    doc.add_paragraph(text, style='Question')
    if options:
        for opt in options: doc.add_paragraph(opt, style='Option')

def add_answer_table(doc, rows):
    table = doc.add_table(rows=1, cols=3); table.alignment = WD_TABLE_ALIGNMENT.CENTER; table.style = 'Light Shading Accent 1'
    for c, t in zip(table.rows[0].cells, ['题号', '答案', '考查要点']): c.text=t; shade(c,'D9EAF7')
    for n, ans, point in rows:
        cells=table.add_row().cells
        for c,t in zip(cells,[str(n),ans,point]): c.text=t; set_cell_margins(c); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER

doc=Document(); sec=doc.sections[0]; sec.top_margin=Cm(1.7); sec.bottom_margin=Cm(1.7); sec.left_margin=Cm(2); sec.right_margin=Cm(2)
styles=doc.styles; normal=styles['Normal']; normal.font.name='等线'; normal._element.rPr.rFonts.set(qn('w:eastAsia'),'等线'); normal.font.size=Pt(11)
for name,size,color in [('Title',20,'1F4E79'),('Heading 1',15,'1F4E79'),('Heading 2',12,'2F75B5')]:
 st=styles[name]; st.font.name='等线'; st._element.rPr.rFonts.set(qn('w:eastAsia'),'等线'); st.font.size=Pt(size); st.font.bold=True; st.font.color.rgb=RGBColor.from_string(color)
for name,size,before,after in [('Question',11,5,1),('Option',10.5,0,0)]:
 st=styles.add_style(name,1); st.font.name='等线'; st._element.rPr.rFonts.set(qn('w:eastAsia'),'等线'); st.font.size=Pt(size); st.paragraph_format.space_before=Pt(before); st.paragraph_format.space_after=Pt(after); st.paragraph_format.line_spacing=1.12
title=doc.add_paragraph(style='Title'); title.alignment=WD_ALIGN_PARAGRAPH.CENTER; title.add_run('2025年广东春季高考数学变式自测卷')
sub=doc.add_paragraph(); sub.alignment=WD_ALIGN_PARAGRAPH.CENTER; sub.add_run('保持原卷知识点与难度梯度 · 题目已改编，用于检验真正掌握情况').italic=True
meta=doc.add_paragraph(); meta.alignment=WD_ALIGN_PARAGRAPH.CENTER; meta.add_run('试卷类型：B    满分：150分    建议用时：90分钟').bold=True
doc.add_paragraph('说明：本卷与原卷保持相同题型结构和考查范围，但更换了数据、情境、函数或图形关系。请独立完成，最后再查看文末答案。')
doc.add_heading('一、选择题（本大题共12小题，每小题6分，共72分）',level=1)
qs=[('1. 已知集合 A={−2,0,1,3}，B={0,1,2,3}，则 A∩B=( )',['A. {−2,0,1,2,3}','B. {0,1,3}','C. {−2,2}','D. {0,1,2,3}']),('2. 复数 z=−2+5i 在复平面内对应的点位于( )',['A. 第一象限','B. 第二象限','C. 第三象限','D. 第四象限']),('3. 函数 f(x)=ln(2x+3) 的定义域是( )',['A. (−∞,−3/2)','B. (−3/2,+∞)','C. [−3/2,+∞)','D. (−∞,−3/2]']),('4. 命题 p：∀x∈R，x²+1>0，则命题 p 的否定是( )',['A. ∀x∈R，x²+1≤0','B. ∀x∈R，x²+1<0','C. ∃x∈R，x²+1≤0','D. ∃x∈R，x²+1<0']),('5. 已知函数 f(x)=2^x，则下列结论正确的是( )',['A. f(−1)>f(0)','B. f(−1)=f(1)','C. f(1)>f(0)','D. f(1)<f(0)']),('6. 已知向量 a=(x,3)，b=(2,−1)，且 a∥b，则 x=( )',['A. −6','B. −3/2','C. 3/2','D. 6']),('7. 某班8名学生一次测验成绩为：72，80，85，88，90，90，94，98。该组数据的第25百分位数为( )',['A. 80','B. 82.5','C. 85','D. 86.5']),('8. 已知 sin(π−α)=3/5，且 α∈(0,π/2)，则 cosα=( )',['A. 3/5','B. 4/5','C. −3/5','D. −4/5']),('9. 将函数 y=sin2x 的图象上所有点向右平移 π/6 个单位长度，所得函数为( )',['A. y=sin(2x+π/3)','B. y=sin(2x−π/3)','C. y=sin(2x+π/6)','D. y=sin(2x−π/6)']),('10. 已知 x>0，y>0，且 x+2y=6，则 xy 的最大值为( )',['A. 9/2','B. 3','C. 2','D. 6']),('11. 设直线 a，b 分别在两个不同平面 α，β 内。若 a 与 b 相交，则“平面 α 与 β 相交”是“直线 a 与 b 相交”的( )',['A. 充分不必要条件','B. 必要不充分条件','C. 充要条件','D. 既不充分也不必要条件']),('12. 已知函数 f(x)=|x−1|−2，若方程 f(x)=k 有两个不同实根，则 k 的取值范围是( )',['A. k>−2','B. k≥−2','C. k<−2','D. −2<k<0'])]
for q,o in qs:add_question(doc,q,o)
doc.add_heading('二、填空题（本大题共6小题，每小题6分，共36分）',level=1)
for f in ['13. 已知事件 A 与 B 相互独立，且 P(A)=0.4，P(B)=0.75，则 P(A∩B)=______。','14. 在平行四边形 ABCD 中，向量 AB=a，向量 AD=b，则向量 AC=______。（用 a，b 表示）','15. 计算：log₂8+log₃9=______。','16. 不等式 (x−2)(x+5)≤0 的解集为______。','17. 已知 tanθ=−2，则 (sinθ+cosθ)/(sinθ−cosθ)=______。（式子有意义）','18. 一个圆锥的母线长为5，底面半径为3，则其母线与底面所成角的正切值为______。']:add_question(doc,f)
doc.add_heading('三、解答题（本大题共4题，共42分）',level=1)
add_question(doc,'19. 已知二次函数 f(x)=ax²+bx+c 的图象经过点 (0,1)、(1,0)、(−1,0)。'); doc.add_paragraph('(1) 求 f(x) 的解析式；\n(2) 判断 f(x) 的奇偶性，并说明理由。')
add_question(doc,'20. 某社区随机抽取10户居民，记录其一周用电量（单位：千瓦时）：18，24，31，27，35，22，29，40，16，28。'); doc.add_paragraph('(1) 求这10户居民一周用电量的平均数；\n(2) 求用电量落在区间 [x̄−8，x̄+8] 内的样本频率，并据此估计该社区900户居民中用电量落在该区间内的户数。')
add_question(doc,'21. 在△ABC中，A=45°，B=60°，边 a=4。'); doc.add_paragraph('(1) 求角 C；\n(2) 求边 b 及边 AC 上中线 BD 的长。')
add_question(doc,'22. 在三棱锥 P−ABC 中，PA⊥平面 ABC，AB⊥BC，PA=3，AB=4，BC=4。'); doc.add_paragraph('(1) 求证：BC⊥平面 PAB；\n(2) 求平面 PBC 与底面 ABC 所成二面角的正切值。')
doc.add_page_break(); doc.add_heading('参考答案（完成试卷后再查看）',level=1)
answers=[(1,'B','集合交集'),(2,'B','复数几何表示'),(3,'B','对数函数定义域'),(4,'C','全称命题的否定'),(5,'C','指数函数单调性'),(6,'A','向量平行'),(7,'B','百分位数'),(8,'B','诱导公式'),(9,'B','三角函数平移'),(10,'A','约束最值'),(11,'A','充分必要条件'),(12,'A','方程根'),(13,'0.3','独立事件概率'),(14,'a+b','向量线性运算'),(15,'5','指数对数运算'),(16,'[−5,2]','二次不等式'),(17,'−3/5','三角化简'),(18,'3/4','圆锥性质'),(19,'f(x)=−x²+1；偶函数','解析式与奇偶性'),(20,'平均27；频率0.7；约630户','统计估算'),(21,'C=75°；b=2√6；BD=√(10+4√3)','解三角形'),(22,'BC⊥平面PAB；tan=3/4','空间垂直与二面角')]
add_answer_table(doc,answers); doc.add_paragraph('自测建议：若答案正确但过程不完整，仍应回到对应知识点重新独立推导；特别关注第12、18、21、22题。')
footer=sec.footer.paragraphs[0]; footer.alignment=WD_ALIGN_PARAGRAPH.CENTER; footer.add_run('广东春季高考数学变式自测卷 · 仅供学习自测').font.size=Pt(9)
doc.core_properties.title='2025年广东春季高考数学变式自测卷'; doc.core_properties.subject='保持原卷知识点与难度的变式练习'; doc.save(OUT); print(OUT)
