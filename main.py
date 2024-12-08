import os
import logging
import uuid
import asyncio
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
)
from sqlalchemy.future import select
from sqlalchemy.orm import relationship
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import (
    ContentType,
    InputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
import redis.asyncio as redis
from dotenv import load_dotenv
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from weasyprint import HTML

load_dotenv()

DEBUG_LEVEL = os.getenv("DEBUG_LEVEL").upper()

db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_host = os.getenv("DB_HOST")
db_port = os.getenv("DB_PORT")
db_name = os.getenv("DB_NAME")

if db_password:
    DATABASE_URL = (
        f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )
else:
    DATABASE_URL = f"postgresql+asyncpg://{db_user}@{db_host}:{db_port}/{db_name}"

logging.basicConfig(level=getattr(logging, DEBUG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

pool_size = int(os.getenv("DB_POOL_SIZE"))
max_overflow = int(os.getenv("DB_MAX_OVERFLOW"))

engine = create_async_engine(
    DATABASE_URL,
    echo=(DEBUG_LEVEL == "DEBUG"),
    pool_size=pool_size,
    max_overflow=max_overflow,
)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher(bot)

if DEBUG_LEVEL == "DEBUG":
    from aiogram.contrib.middlewares.logging import LoggingMiddleware

    dp.middleware.setup(LoggingMiddleware())

redis_host = os.getenv("REDIS_HOST")
redis_port = int(os.getenv("REDIS_PORT"))
redis_password = os.getenv("REDIS_PASSWORD")
redis_db = int(os.getenv("REDIS_DB"))

if redis_password:
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        db=redis_db,
        decode_responses=True,
    )
else:
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        db=redis_db,
        decode_responses=True,
    )


@asynccontextmanager
async def get_db_session():
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def on_startup(dispatcher):
    await init_db()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    available_audits = Column(Integer, default=0)
    audit_requests = relationship("AuditRequest", back_populates="user")


class AuditRequest(Base):
    __tablename__ = "audit_requests"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    report_path = Column(String, nullable=True)
    status = Column(String, default="queued")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="audit_requests")


QUEUE_KEY = "audit_queue"


async def get_or_create_user(session: AsyncSession, message: types.Message) -> User:
    telegram_id = message.from_user.id
    user = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = user.scalars().first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username,
            available_audits=0,
        )
        session.add(user)
        await session.commit()
        logger.info(f"Создан новый пользователь: {telegram_id}")
    return user


@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    async with get_db_session() as session:
        user = await get_or_create_user(session, message)
        await session.commit()

    user_first_name = message.from_user.first_name
    if user_first_name:
        await message.reply(
            f"👋 Привет, {user_first_name}! Рад тебя видеть.\n\n"
            "Я бот для аудита смарт-контрактов на Solidity. 📄🔍\n"
            "Ты можешь использовать следующие команды, чтобы начать работу со мной:"
        )
    else:
        await message.reply(
            "👋 Привет! Рад тебя видеть.\n\n"
            "Я бот для аудита смарт-контрактов на Solidity. 📄🔍\n"
            "Ты можешь использовать следующие команды, чтобы начать работу со мной:"
        )


@dp.message_handler(commands=["about"])
async def cmd_about(message: types.Message):
    await message.reply(
        "ℹ️ *О боте и доступных командах:*\n\n"
        "👋 */start* - Начать работу с ботом и получить приветствие.\n"
        "ℹ️ */about* - Получить информацию о командах и возможностях бота. 📚\n"
        "📄 */audit* - Провести аудит смарт-контракта. Для этого отправьте файл с расширением `.sol` вместе с командой.\n"
        "🆓 */free* - Получить бесплатные аудиты (2 аудита для новых пользователей).\n"
        "🛒 */buy {кол-во}* - Купить дополнительные аудиты.\n"
        "📊 */status* - Проверить количество доступных аудитов.\n\n"
        "💡 *Что умеет бот:*\n"
        "- Принимает файлы смарт-контрактов на Solidity (.sol) для проведения аудита. 🛡️\n"
        "- Проверяет файл на корректность формата. ✔️\n"
        "- Генерирует подробный HTML и PDF отчеты по аудиту и отправляет их пользователю. 📑📝\n"
        "- Использует уникальные временные хранилища для обработки файлов разных пользователей одновременно. 🔄\n"
        "- Управляет очередью запросов на аудит и уведомляет пользователей о позиции в очереди. 🕒\n\n"
        "Если у тебя есть вопросы или предложения, не стесняйся обращаться! 😊",
        parse_mode=types.ParseMode.MARKDOWN,
    )


@dp.message_handler(commands=["free"])
async def cmd_free(message: types.Message):
    async with get_db_session() as session:
        user = await get_or_create_user(session, message)
        if user.available_audits == 0:
            user.available_audits += 2
            await session.commit()
            await message.reply(
                "🎁 Вам начислено 2 бесплатных аудита! Вы можете использовать их с помощью команды `/audit`. 😊",
                parse_mode=types.ParseMode.MARKDOWN,
            )
        else:
            await message.reply(
                "🛑 Вы уже получили бесплатные аудиты. Если хотите приобрести дополнительные аудиты, используйте команду `/buy {кол-во}`. 🛒",
                parse_mode=types.ParseMode.MARKDOWN,
            )


@dp.message_handler(commands=["buy"])
async def cmd_buy(message: types.Message):
    args = message.get_args()
    if not args:
        await message.reply(
            "❗️ Пожалуйста, укажите количество аудитов, которые вы хотите купить.\n"
            "Пример: `/buy 5`",
            parse_mode=types.ParseMode.MARKDOWN,
        )
        return

    try:
        quantity = int(args)
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await message.reply(
            "❌ Пожалуйста, укажите корректное положительное целое число для покупки аудитов.\n"
            "Пример: `/buy 5`",
            parse_mode=types.ParseMode.MARKDOWN,
        )
        return

    keyboard = InlineKeyboardMarkup(row_width=2)
    pay_button = InlineKeyboardButton(text="💳 Оплатить", url="https://www.google.com")
    check_button = InlineKeyboardButton(
        text="✅ Проверить оплату", callback_data=f"check_payment:{quantity}"
    )
    keyboard.add(pay_button, check_button)

    await message.reply(
        f"🛒 Вы собираетесь купить {quantity} аудита(ов).\n"
        "Пожалуйста, выберите действие ниже:",
        reply_markup=keyboard,
    )


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("check_payment:"))
async def callback_check_payment(callback_query: types.CallbackQuery):
    try:
        _, quantity_str = callback_query.data.split(":")
        quantity = int(quantity_str)
    except (IndexError, ValueError):
        await callback_query.answer(
            "❌ Некорректные данные для проверки.", show_alert=True
        )
        return

    async with get_db_session() as session:
        user = await session.execute(
            select(User).where(User.telegram_id == callback_query.from_user.id)
        )
        user = user.scalars().first()
        if not user:
            await callback_query.answer("❌ Пользователь не найден.", show_alert=True)
            return

        user.available_audits += quantity
        await session.commit()

    await callback_query.answer(
        "✅ Оплата подтверждена! Ваши аудиты зачислены. 🎉", show_alert=True
    )
    await bot.send_message(
        callback_query.from_user.id,
        f"🎉 Вам успешно зачислено {quantity} аудита(ов)! Теперь у вас доступно {user.available_audits} аудита(ов).",
    )


@dp.message_handler(commands=["status"])
async def cmd_status(message: types.Message):
    async with get_db_session() as session:
        user = await get_or_create_user(session, message)
        await session.commit()

    await message.reply(
        f"📊 *Статус вашего аккаунта:*\n"
        f"🔢 Доступные аудиты: {user.available_audits}\n"
        "📈 Вы можете использовать команду `/audit`, чтобы провести аудит вашего смарт-контракта.\n"
        "🛒 Используйте команду `/buy {кол-во}`, чтобы приобрести дополнительные аудиты.\n"
        "🆓 Или получите бесплатные аудиты с помощью команды `/free`.",
        parse_mode=types.ParseMode.MARKDOWN,
    )


@dp.message_handler(commands=["audit"], content_types=ContentType.DOCUMENT)
async def cmd_audit(message: types.Message):
    document = message.document
    file_name = document.file_name
    file_extension = os.path.splitext(file_name)[1].lower()

    if file_extension != ".sol":
        await message.reply(
            "❌ Пожалуйста, отправьте файл с расширением `.sol` для аудита смарт-контракта."
        )
        return

    async with get_db_session() as session:
        user = await get_or_create_user(session, message)
        if user.available_audits <= 0:
            await message.reply(
                "❌ У вас нет доступных аудитов. Пожалуйста, приобретите дополнительные аудиты с помощью команды `/buy {кол-во}` или получите бесплатные аудиты с помощью `/free`."
            )
            return

        user.available_audits -= 1

        unique_id = uuid.uuid4().hex
        uploads_dir = "uploads"
        reports_dir = "reports"
        os.makedirs(uploads_dir, exist_ok=True)
        os.makedirs(reports_dir, exist_ok=True)

        file_path = os.path.join(uploads_dir, f"{unique_id}.sol")
        report_html_path = os.path.join(reports_dir, f"{unique_id}_report.html")
        report_pdf_path = os.path.join(reports_dir, f"{unique_id}_report.pdf")

        audit_request = AuditRequest(
            user_id=user.id,
            file_name=file_name,
            file_path=file_path,
            report_path=report_html_path,
            status="queued",
        )
        session.add(audit_request)
        await session.commit()

        await redis_client.rpush(QUEUE_KEY, audit_request.id)

        queue_length = await redis_client.llen(QUEUE_KEY)
        position_in_queue = queue_length

    await message.reply(
        f"✅ Ваш файл прошел проверку и добавлен в очередь на аудит! 🔍\n"
        f"📋 Ваша позиция в очереди: {position_in_queue}\n"
        "Пожалуйста, подождите немного. 🕒"
    )


@dp.message_handler(commands=["audit"])
async def audit_no_file(message: types.Message):
    await message.reply(
        "❗️ Для проведения аудита, пожалуйста, отправьте файл с расширением `.sol` вместе с командой /audit.\n"
        "Например: `/audit` и прикрепите ваш файл. 📂",
        parse_mode=types.ParseMode.MARKDOWN,
    )


async def process_audit_queue():
    while True:
        audit_request_id = await redis_client.lpop(QUEUE_KEY)
        if audit_request_id:
            async with get_db_session() as session:
                audit_request = await session.get(AuditRequest, int(audit_request_id))
                if audit_request and audit_request.status == "queued":
                    audit_request.status = "processing"
                    await session.commit()

                    user = await session.get(User, audit_request.user_id)
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            f"🔄 Начинаю аудит вашего смарт-контракта: {audit_request.file_name}",
                        )

                        file = await bot.get_file(audit_request.file_path)
                        downloaded_file = await bot.download_file(file.file_path)
                        with open(audit_request.file_path, "wb") as f:
                            f.write(downloaded_file.read())

                        html_content = generate_html_report(audit_request.file_name)
                        with open(
                            audit_request.report_path, "w", encoding="utf-8"
                        ) as report_file:
                            report_file.write(html_content)

                        try:
                            HTML(audit_request.report_path).write_pdf(
                                audit_request.report_path.replace(".html", ".pdf")
                            )
                            report_final_path = audit_request.report_path.replace(
                                ".html", ".pdf"
                            )
                        except Exception as pdf_error:
                            logger.error(
                                f"Ошибка при конвертации HTML в PDF: {pdf_error}"
                            )
                            report_final_path = audit_request.report_path

                        await bot.send_document(
                            user.telegram_id,
                            InputFile(report_final_path),
                            caption="📄 Ваш отчет по аудиту смарт-контракта готов! Вы можете просмотреть его ниже.",
                        )

                        audit_request.status = "completed"
                        await session.commit()

                        try:
                            os.remove(audit_request.file_path)
                            os.remove(audit_request.report_path)
                            os.remove(report_final_path)
                        except Exception as cleanup_error:
                            logger.error(
                                f"Ошибка при удалении временных файлов: {cleanup_error}"
                            )

                        await bot.send_message(
                            user.telegram_id, "✅ Аудит завершен успешно! 🎉"
                        )

                    except Exception as e:
                        logger.error(
                            f"Ошибка при аудите запроса {audit_request.id}: {e}"
                        )
                        audit_request.status = "failed"
                        await session.commit()
                        await bot.send_message(
                            user.telegram_id,
                            "❌ Произошла ошибка при проведении аудита. Пожалуйста, попробуйте позже.",
                        )
        else:
            await asyncio.sleep(5)


def generate_html_report(file_name: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Отчет по аудиту смарт-контракта</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1, h2, h3 {{ color: #2E86C1; }}
            .page {{ page-break-after: always; }}
            .title-page {{ text-align: center; margin-top: 100px; }}
            .content {{ margin-top: 50px; }}
            .section {{ margin-bottom: 40px; }}
            ul {{ list-style-type: disc; margin-left: 20px; }}
        </style>
    </head>
    <body>
        <!-- Титульный лист -->
        <div class="page title-page">
            <h1>Отчет по аудиту смарт-контракта</h1>
            <p>Дата: {datetime.now(timezone.utc).strftime('%d.%m.%Y')}</p>
            <p>Контракт: {file_name}</p>
        </div>

        <!-- О команде -->
        <div class="page content">
            <h2>О нашей команде</h2>
            <p>Мы — команда опытных аудиторов, специализирующихся на проверке смарт-контрактов на Solidity. 🛡️</p>
            <p>Наша миссия — обеспечить безопасность и надежность ваших блокчейн-проектов. 🚀</p>
            <p>Мы используем современные инструменты и методологии для проведения тщательного анализа кода, выявления уязвимостей и предоставления рекомендаций по улучшению. 🧰</p>
        </div>

        <!-- Цели и задачи аудита -->
        <div class="page content">
            <h2>Цели и задачи аудита</h2>
            <p>Основные цели аудита смарт-контракта:</p>
            <ul>
                <li>Идентификация потенциальных уязвимостей и ошибок в коде. 🔍</li>
                <li>Оценка безопасности логики контракта. 🛡️</li>
                <li>Предоставление рекомендаций по улучшению и оптимизации кода. 📝</li>
                <li>Обеспечение соответствия контракта лучшим практикам разработки. 📈</li>
            </ul>
        </div>

        <!-- Поиск уязвимостей и рекомендации -->
        <div class="page content">
            <h2>Поиск уязвимостей и рекомендации</h2>
            <p>В ходе аудита были проведены следующие проверки:</p>
            <ul>
                <li>Анализ кода на наличие стандартных уязвимостей, таких как reentrancy, integer overflow/underflow, неправильное управление доступом и другие. ⚠️</li>
                <li>Оценка логики смарт-контракта для выявления потенциальных ошибок и недочетов в бизнес-логике. 🧠</li>
                <li>Проверка на соответствие лучшим практикам разработки, включая использование проверенных библиотек и шаблонов проектирования. 📚</li>
                <li>Тестирование функциональности контракта с использованием автоматизированных инструментов и ручных проверок. 🛠️</li>
            </ul>
            <p>Рекомендации:</p>
            <ul>
                <li>Использовать последние версии Solidity для повышения безопасности и производительности. 📦</li>
                <li>Добавить дополнительные проверки ввода данных для предотвращения некорректных операций. ✅</li>
                <li>Рассмотреть возможность проведения регулярных аудитов и тестирования после внесения изменений в код. 🔄</li>
                <li>Внедрить системы мониторинга и оповещений для своевременного обнаружения аномалий и потенциальных атак. 📊</li>
            </ul>
        </div>

        <!-- Тестирование и верификация -->
        <div class="page content">
            <h2>Тестирование и верификация</h2>
            <p>Мы провели обширное тестирование смарт-контракта, включая:</p>
            <ul>
                <li>Юнит-тестирование всех функций контракта с использованием фреймворков для тестирования Solidity. 🧪</li>
                <li>Интеграционное тестирование для проверки взаимодействия контракта с другими смарт-контрактами и системами. 🔗</li>
                <li>Формальная верификация для доказательства корректности выполнения ключевых функций контракта. 📜</li>
                <li>Стресс-тестирование для оценки производительности и устойчивости контракта под высокой нагрузкой. 🚀</li>
            </ul>
        </div>

        <!-- Безопасность и соответствие стандартам -->
        <div class="page content">
            <h2>Безопасность и соответствие стандартам</h2>
            <p>Наш аудит включает проверку соответствия контракта следующим стандартам и рекомендациям:</p>
            <ul>
                <li>ERC-20 и ERC-721 стандарты для токенов, обеспечивающие совместимость и безопасность. 🪙</li>
                <li>Использование OpenZeppelin библиотек для повышения надежности и безопасности кода. 🛡️</li>
                <li>Следование рекомендациям по безопасности от Ethereum и других блокчейн-сообществ. 📖</li>
            </ul>
        </div>

        <!-- Заключение -->
        <div class="page content">
            <h2>Заключение</h2>
            <p>Ваш смарт-контракт прошел тщательную проверку и не содержит критических уязвимостей. 🎉</p>
            <p>Однако, для повышения общей безопасности и эффективности вашего проекта, мы рекомендуем следовать нашим рекомендациям. 🛠️</p>
            <p>Мы готовы предоставить дополнительную поддержку и ответить на любые вопросы, связанные с вашим смарт-контрактом. 🤝</p>
            <p>Спасибо за использование нашего сервиса! 😊</p>
        </div>
    </body>
    </html>
    """


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("check_payment:"))
async def callback_check_payment(callback_query: types.CallbackQuery):
    try:
        _, quantity_str = callback_query.data.split(":")
        quantity = int(quantity_str)
    except (IndexError, ValueError):
        await callback_query.answer(
            "❌ Некорректные данные для проверки.", show_alert=True
        )
        return

    async with get_db_session() as session:
        user = await session.execute(
            select(User).where(User.telegram_id == callback_query.from_user.id)
        )
        user = user.scalars().first()
        if not user:
            await callback_query.answer("❌ Пользователь не найден.", show_alert=True)
            return

        user.available_audits += quantity
        await session.commit()

    await callback_query.answer(
        "✅ Оплата подтверждена! Ваши аудиты зачислены. 🎉", show_alert=True
    )
    await bot.send_message(
        callback_query.from_user.id,
        f"🎉 Вам успешно зачислено {quantity} аудита(ов)! Теперь у вас доступно {user.available_audits} аудита(ов).",
    )


@dp.message_handler(commands=["buy"])
async def cmd_buy(message: types.Message):
    args = message.get_args()
    if not args:
        await message.reply(
            "❗️ Пожалуйста, укажите количество аудитов, которые вы хотите купить.\n"
            "Пример: `/buy 5`",
            parse_mode=types.ParseMode.MARKDOWN,
        )
        return

    try:
        quantity = int(args)
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await message.reply(
            "❌ Пожалуйста, укажите корректное положительное целое число для покупки аудитов.\n"
            "Пример: `/buy 5`",
            parse_mode=types.ParseMode.MARKDOWN,
        )
        return

    keyboard = InlineKeyboardMarkup(row_width=2)
    pay_button = InlineKeyboardButton(text="💳 Оплатить", url="https://www.google.com")
    check_button = InlineKeyboardButton(
        text="✅ Проверить оплату", callback_data=f"check_payment:{quantity}"
    )
    keyboard.add(pay_button, check_button)

    await message.reply(
        f"🛒 Вы собираетесь купить {quantity} аудита(ов).\n"
        "Пожалуйста, выберите действие ниже:",
        reply_markup=keyboard,
    )


async def process_audit_queue():
    while True:
        audit_request_id = await redis_client.lpop(QUEUE_KEY)
        if audit_request_id:
            async with get_db_session() as session:
                audit_request = await session.get(AuditRequest, int(audit_request_id))
                if audit_request and audit_request.status == "queued":
                    audit_request.status = "processing"
                    await session.commit()

                    user = await session.get(User, audit_request.user_id)
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            f"🔄 Начинаю аудит вашего смарт-контракта: {audit_request.file_name}",
                        )

                        file = await bot.get_file(audit_request.file_path)
                        downloaded_file = await bot.download_file(file.file_path)
                        with open(audit_request.file_path, "wb") as f:
                            f.write(downloaded_file.read())

                        html_content = generate_html_report(audit_request.file_name)
                        with open(
                            audit_request.report_path, "w", encoding="utf-8"
                        ) as report_file:
                            report_file.write(html_content)

                        try:
                            HTML(audit_request.report_path).write_pdf(
                                audit_request.report_path.replace(".html", ".pdf")
                            )
                            report_final_path = audit_request.report_path.replace(
                                ".html", ".pdf"
                            )
                        except Exception as pdf_error:
                            logger.error(
                                f"Ошибка при конвертации HTML в PDF: {pdf_error}"
                            )
                            report_final_path = audit_request.report_path

                        await bot.send_document(
                            user.telegram_id,
                            InputFile(report_final_path),
                            caption="📄 Ваш отчет по аудиту смарт-контракта готов! Вы можете просмотреть его ниже.",
                        )

                        audit_request.status = "completed"
                        await session.commit()

                        try:
                            os.remove(audit_request.file_path)
                            os.remove(audit_request.report_path)
                            os.remove(report_final_path)
                        except Exception as cleanup_error:
                            logger.error(
                                f"Ошибка при удалении временных файлов: {cleanup_error}"
                            )

                        await bot.send_message(
                            user.telegram_id, "✅ Аудит завершен успешно! 🎉"
                        )

                    except Exception as e:
                        logger.error(
                            f"Ошибка при аудите запроса {audit_request.id}: {e}"
                        )
                        audit_request.status = "failed"
                        await session.commit()
                        await bot.send_message(
                            user.telegram_id,
                            "❌ Произошла ошибка при проведении аудита. Пожалуйста, попробуйте позже.",
                        )
        else:
            await asyncio.sleep(5)


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
