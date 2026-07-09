import secrets
from typing import Any, Dict, List


class BaseGame:
    def __init__(self, game_id, creator, players):
        self.game_id = game_id
        self.creator = creator
        self.players = players
        self.finished = False
        self.winner = None

    def handle_action(self, player, action_data):
        """处理动作，返回 (success, message)"""
        raise NotImplementedError

    def get_state(self):
        """返回当前游戏状态（用于显示或发送）"""
        raise NotImplementedError

    def is_finished(self):
        return self.finished

    def get_winner(self):
        return self.winner

    def get_participants(self):
        return list(self.players)

    @staticmethod
    def _as_int(value, default=0):
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(value)
        except Exception:
            return default


class GomokuGame(BaseGame):
    def __init__(self, game_id, creator, players):
        super().__init__(game_id, creator, players)
        self.board = [["" for _ in range(15)] for _ in range(15)]
        self.current_player = creator
        self.move_count = 0
        self.symbols = {
            players[0]: 'X',
            players[1] if len(players) > 1 else creator: 'O'
        }
        self.player_order = [players[0]]
        if len(players) > 1:
            self.player_order.append(players[1])

    def handle_action(self, player, action_data):
        if self.finished:
            return False, "游戏已结束"
        if player != self.current_player:
            return False, "当前不轮到你下棋"
        if isinstance(action_data, str):
            parts = action_data.strip().split()
            if len(parts) != 3 or parts[0].lower() != 'move':
                return False, "落子格式应为: move x y"
            try:
                x = int(parts[1])
                y = int(parts[2])
            except ValueError:
                return False, "坐标必须为数字"
        elif isinstance(action_data, dict):
            x = self._as_int(action_data.get('x'), -1)
            y = self._as_int(action_data.get('y'), -1)
        else:
            return False, "无效的动作数据"
        if x < 1 or x > 15 or y < 1 or y > 15:
            return False, "坐标必须在1到15之间"
        board_x = x - 1
        board_y = y - 1
        if self.board[board_y][board_x] != "":
            return False, "该位置已有棋子"
        symbol = self.symbols.get(player, 'X')
        self.board[board_y][board_x] = symbol
        self.move_count += 1
        if self._check_win(board_x, board_y):
            self.finished = True
            self.winner = player
            return True, f"玩家 {player} 获胜！"
        if len(self.player_order) == 2:
            self.current_player = self.player_order[1] if self.current_player == self.player_order[0] else self.player_order[0]
        return True, f"落子成功: ({x}, {y})，下一位: {self.current_player}"

    def _check_win(self, x, y):
        symbol = self.board[y][x]
        if symbol == "":
            return False
        directions = [
            (1, 0),
            (0, 1),
            (1, 1),
            (1, -1),
        ]
        for dx, dy in directions:
            count = 1
            nx, ny = x + dx, y + dy
            while 0 <= nx < 15 and 0 <= ny < 15 and self.board[ny][nx] == symbol:
                count += 1
                nx += dx
                ny += dy
            nx, ny = x - dx, y - dy
            while 0 <= nx < 15 and 0 <= ny < 15 and self.board[ny][nx] == symbol:
                count += 1
                nx -= dx
                ny -= dy
            if count >= 5:
                return True
        return False

    def get_state(self):
        return {
            "type": "gomoku",
            "game_id": self.game_id,
            "creator": self.creator,
            "players": list(self.players),
            "board": self.board,
            "current_player": self.current_player,
            "finished": self.finished,
            "winner": self.winner,
            "move_count": self.move_count,
        }


class GuessNumberGame(BaseGame):
    def __init__(self, game_id, creator, players, range_low=1, range_high=100, max_attempts=10):
        super().__init__(game_id, creator, players)
        self.range_low = range_low
        self.range_high = range_high
        self.max_attempts = max_attempts
        self.attempts = {p: 0 for p in players}
        self.guessed = set()
        self.guess_history = []
        self.secret_number = secrets.randbelow(range_high - range_low + 1) + range_low

    def handle_action(self, player, action_data):
        if self.finished:
            return False, "游戏已结束"
        if isinstance(action_data, str):
            parts = action_data.strip().split()
            if len(parts) != 2 or parts[0].lower() != 'guess':
                return False, "猜数格式应为: guess n"
            try:
                guess = int(parts[1])
            except ValueError:
                return False, "猜测必须为数字"
        elif isinstance(action_data, dict):
            raw = action_data.get('guess')
            if raw is None:
                return False, "猜测必须为数字"
            try:
                guess = int(raw)
            except Exception:
                return False, "猜测必须为数字"
        else:
            return False, "无效的动作数据"
        if guess < self.range_low or guess > self.range_high:
            return False, f"猜测必须在 {self.range_low}-{self.range_high} 之间"
        if guess in self.guessed:
            return False, "该数字已被猜过"
        self.guessed.add(guess)
        self.guess_history.append({"player": player, "guess": guess})
        self.attempts[player] = self.attempts.get(player, 0) + 1
        if guess == self.secret_number:
            self.finished = True
            self.winner = player
            return True, f"恭喜 {player} 猜中了数字 {guess}，获胜！"
        if self.attempts.get(player, 0) >= self.max_attempts:
            all_exhausted = all(self.attempts.get(p, 0) >= self.max_attempts for p in self.players)
            if all_exhausted:
                self.finished = True
                return True, f"所有玩家次数用尽，游戏结束。正确答案是 {self.secret_number}。"
            else:
                return True, f"{player} 的尝试次数已用尽。其他玩家仍可继续。"
        hint = "偏大" if guess > self.secret_number else "偏小"
        remaining = self.max_attempts - self.attempts.get(player, 0)
        return True, f"{player} 的猜测 {guess} {hint}，剩余次数 {remaining}"

    def get_state(self):
        return {
            "type": "guess",
            "game_id": self.game_id,
            "creator": self.creator,
            "players": list(self.players),
            "range_low": self.range_low,
            "range_high": self.range_high,
            "max_attempts": self.max_attempts,
            "attempts": self.attempts,
            "guess_history": self.guess_history,
            "finished": self.finished,
            "winner": self.winner,
        }
