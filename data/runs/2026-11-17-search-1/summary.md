# Опис прогону `2026-11-17-search-1`

Вікно дат: 2020-01-01 — 2026-11-17

## 1. Потік до скринінгу

| Етап | Записів |
|---|---|
| Знайдено в базах (сирі записи) | 763 |
| Дублікатів вилучено | 287 |
| Кандидатів на скринінг | 476 |

## 2. Бази даних

`не проходить предикат` — база повернула запис, але той самий булевий рядок локально для назви й анотації не виконується.

| База | Статус | Записів | Не проходить предикат | Без анотації |
|---|---|---|---|---|
| openalex | ok | 483 | 35 (8%) | 41 (8%) |
| semantic_scholar | ok | 199 | 17 (9%) | 13 (7%) |
| arxiv | ok | 81 | 76 (94%) | 0 (0%) |
| ieee_xplore | skipped: IEEE_API_KEY not set | 0 | 0 (—) | 0 (—) |
| springer_link | skipped: SPRINGER_API_KEY not set | 0 | 0 (—) | 0 (—) |
| sciencedirect | skipped: ELSEVIER_API_KEY not set | 0 | 0 (—) | 0 (—) |

## 3. Перетин баз

| Бази | Кандидатів |
|---|---|
| openalex | 225 |
| openalex;semantic_scholar | 165 |
| arxiv | 74 |
| arxiv;openalex;semantic_scholar | 7 |
| semantic_scholar | 5 |

Кандидатів, знайдених лише однією базою: openalex — 225, arxiv — 74, semantic_scholar — 5

## 4. Запити й автоматичні позначки

| Запит | Кандидатів |
|---|---|
| primary | 381 |
| organizational | 95 |
| concepts | 31 |
| tools | 12 |
| metrics | 8 |

| Позначка | Кандидатів | Частка |
|---|---|---|
| pe-in-title | 145 | 30% |
| primary-match | 143 | 30% |
| no-match | 106 | 22% |
| facet-match | 67 | 14% |
| no-match-title-only | 15 | 3% |

## 5. Роки публікації

| Рік | Кандидатів |
|---|---|
| 2020 | 22 |
| 2021 | 20 |
| 2022 | 22 |
| 2023 | 26 |
| 2024 | 40 |
| 2025 | 122 |
| 2026 | 224 |

## 6. Видання

Сховища препринтів: 161 (34%); видання не вказано: 10 (2%).

20 найчастіших видань (рецензованість перевіряється вручну за DBLP/Scopus, результат — у `data/quality/venues.csv`):

| Видання | Кандидатів | Препринт |
|---|---|---|
| arXiv | 74 | так |
| Zenodo (CERN European Organization for Nuclear Research) | 66 | так |
| SSRN Electronic Journal | 16 | так |
| Journal of Information Systems Engineering & Management | 10 |  |
| Wiley eBooks | 10 |  |
| Journal of Computer Science and Technology Studies | 8 |  |
| World Journal of Advanced Engineering Technology and Sciences | 7 |  |
| International Journal of AI BigData Computational and Management Studies | 5 |  |
| International Journal of Science and Research (IJSR) | 5 |  |
| Apress eBooks | 5 |  |
| International Journal of Computational and Experimental Science and Engineering | 5 |  |
| International Journal of Intelligent Systems and Applications in Engineering | 5 |  |
| International Journal of Scientific Research and Engineering Trends | 4 |  |
| International Journal of Scientific Research in Computer Science Engineering and Information Technology | 4 |  |
| Journal of Science & Technology | 4 |  |
| Lecture notes in networks and systems | 4 |  |
| World Journal of Advanced Research and Reviews | 4 |  |
| Iconic Research and Engineering Journals | 4 |  |
| International Journal of Science Engineering and Technology | 3 |  |
| International Journal For Multidisciplinary Research | 3 |  |

## 7. Авторські кластери

Кандидатів з відомим першим автором: 472; незалежних авторських груп: 405.

Перші автори з трьома й більше кандидатами:

| Перший автор | Кандидатів | Приклад назви |
|---|---|---|
| Baba Prasad Pendyala | 8 | THE CONVERGENCE OF PLATFORM ENGINEERING AND SERVICE AUTOMATION: TRANSFORMING ENT |
| Hall D | 7 | ARCHITECTURAL PATTERNS AND COMPONENTS OF INTERNAL DEVELOPER PLATFORMS: EVIDENCE  |
| Sonu Kumar Singh | 5 | P249: AI platform engineering as an internal product: Google Cloud study |
| Yoko(洋子) Hasebe(長谷部) | 5 | 全著作物・全Zenodo論文(320本以上)に関する包括的AIライセンスおよび無断学習禁止規定 / Omnibus Sovereign IP Declarati |
| Dharmendra Ahuja | 4 | A Platform Engineering Maturity Model For Cloud- Native Enterprise Transformatio |
| Bhanu Kiran Kumar Muggalla | 4 | Evaluating the Impact of Internal Developer Platforms on Developer Productivity  |
| Leonardo Leite | 4 | The Organization of Software Teams in the Quest for Continuous Delivery: A Groun |
| Pranay Kale | 3 | A Deep Learning-Based Platform Engineering Framework for Predictive CI/CD Pipeli |
| Ajay Chankramath | 3 | The Platform Engineering Renaissance |
| Naga Krishna Reddy Muppidi | 3 | Evidence-Graph-Based Continuous Attestation for AWS Platform Foundations |
| Sanjeeve Kumar Gajadi | 3 | SAP BTP Enterprise Architecture Decision Framework |
| Daniel Rosehill | 3 | Building a $180 Privacy-First AI Wearable |
| Yoko Hasebe | 3 | Classification: Computer Systems Organization (Real-time systems, Fault-tolerant |

## 8. Відомі записи (перевірка повноти)

| DOI | Знайдено |
|---|---|
| 10.3389/fcomp.2026.1814498 | ✓ |
| 10.1109/access.2025.3564141 | ✓ |
| 10.1007/978-3-031-56862-6_1 | ✓ |

Знайдено 3 з 3.
