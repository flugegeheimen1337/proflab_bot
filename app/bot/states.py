from aiogram.fsm.state import State, StatesGroup


class BroadcastForm(StatesGroup):
    audience = State()
    timing = State()
    content = State()
    schedule = State()
    confirm = State()
