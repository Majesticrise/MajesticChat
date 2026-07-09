import time
from typing import Any, Dict

from games import GomokuGame, GuessNumberGame


class GameManager:
    def __init__(self, node):
        self.node = node
        self.rooms = {}
        self.participants = {}
        self.active_games = {}
        self.game_records = {}
        self.player_to_game = {}
        self.game_counter = 0
        self.cleanup_interval = 300

    def add_room(self, game_name, host_username, host_ip, port):
        self.rooms[game_name] = {
            'host_username': host_username,
            'host_ip': host_ip,
            'port': port,
            'timestamp': time.time()
        }
        self.participants[game_name] = {host_username}

    def remove_room(self, game_name):
        if game_name in self.rooms:
            del self.rooms[game_name]
        if game_name in self.participants:
            del self.participants[game_name]

    def get_room(self, game_name):
        return self.rooms.get(game_name)

    def list_rooms(self):
        now = time.time()
        expired = [name for name, info in self.rooms.items()
                   if now - info['timestamp'] > self.cleanup_interval]
        for name in expired:
            self.remove_room(name)
        return self.rooms.copy()

    def add_participant(self, game_name, username):
        if game_name in self.participants:
            self.participants[game_name].add(username)
            return True
        return False

    def remove_participant(self, game_name, username):
        if game_name not in self.participants:
            return False
        self.participants[game_name].discard(username)
        if not self.participants[game_name]:
            self.remove_room(game_name)
            return True
        return False

    def get_participants(self, game_name):
        if game_name in self.participants:
            return self.participants[game_name].copy()
        return set()

    def create_game(self, game_type, creator, *args):
        self.game_counter += 1
        game_id = f"g{self.game_counter}"
        if game_type == 'gomoku':
            opponent = args[0] if args and isinstance(args[0], str) else None
            players = [creator]
            if opponent and opponent != creator:
                players.append(opponent)
            game = GomokuGame(game_id, creator, players)
        elif game_type == 'guess':
            players = [creator]
            range_low = 1
            range_high = 100
            max_attempts = 10
            if args and isinstance(args[0], str):
                spec = args[0].strip()
                if '-' in spec:
                    parts = spec.split('-', 1)
                    try:
                        range_low = int(parts[0])
                        range_high = int(parts[1])
                    except ValueError:
                        pass
            if args and len(args) > 1 and isinstance(args[1], int):
                max_attempts = args[1]
            game = GuessNumberGame(game_id, creator, players, range_low, range_high, max_attempts)
        else:
            raise ValueError("未知游戏类型")
        self.active_games[game_id] = game
        self.player_to_game[creator] = game_id
        self.game_records[game_id] = {
            'game_id': game_id,
            'type': game_type,
            'creator': creator,
            'creator_ip': self.node.self_ip,
            'participants': game.get_participants(),
            'finished': False,
        }
        return game

    def get_game(self, game_id):
        return self.active_games.get(game_id)

    def get_game_record(self, game_id):
        return self.game_records.get(game_id)

    def list_game_records(self):
        return {gid: info.copy() for gid, info in self.game_records.items()}

    def add_game_record(self, game_id, game_type, creator, creator_ip, participants, finished=False):
        self.game_records[game_id] = {
            'game_id': game_id,
            'type': game_type,
            'creator': creator,
            'creator_ip': creator_ip,
            'participants': list(participants) if isinstance(participants, (list, set, tuple)) else [],
            'finished': bool(finished),
        }

    def update_game_record(self, game_id, **kwargs):
        if game_id not in self.game_records:
            return
        for key, value in kwargs.items():
            if key == 'participants' and isinstance(value, (list, set, tuple)):
                self.game_records[game_id][key] = list(value)
            elif key in self.game_records[game_id]:
                self.game_records[game_id][key] = value

    def add_game_participant(self, game_id, username):
        if username in self.player_to_game and self.player_to_game[username] != game_id:
            return False
        game = self.get_game(game_id)
        if game:
            if isinstance(game, GomokuGame):
                if username not in game.players:
                    if len(game.players) >= 2:
                        return False
                    game.players.append(username)
                    game.symbols[username] = 'O' if game.players[0] != username else 'X'
                    if len(game.player_order) == 1:
                        game.player_order.append(username)
                self.player_to_game[username] = game_id
                self.update_game_record(game_id, participants=game.get_participants())
                return True
            if username not in game.players:
                game.players.append(username)
            self.player_to_game[username] = game_id
            self.update_game_record(game_id, participants=game.get_participants())
            return True
        record = self.get_game_record(game_id)
        if record:
            participants = record.get('participants', [])
            if username in participants:
                return True
            if record.get('type') == 'gomoku' and len(participants) >= 2:
                return False
            participants.append(username)
            self.update_game_record(game_id, participants=participants)
            return True
        return False

    def remove_game_participant(self, game_id, username):
        game = self.get_game(game_id)
        if game:
            if username in game.players:
                game.players.remove(username)
            if self.player_to_game.get(username) == game_id:
                del self.player_to_game[username]
            self.update_game_record(game_id, participants=game.get_participants())
            if not game.players:
                self.remove_game(game_id)
                return True
            return True
        record = self.get_game_record(game_id)
        if record:
            participants = record.get('participants', [])
            if username in participants:
                participants.remove(username)
                self.update_game_record(game_id, participants=participants)
            return True
        return False

    def remove_game_record(self, game_id):
        if game_id in self.game_records:
            del self.game_records[game_id]

    def remove_game(self, game_id):
        game = self.active_games.pop(game_id, None)
        if game:
            for username in list(game.players):
                if self.player_to_game.get(username) == game_id:
                    del self.player_to_game[username]
        self.remove_game_record(game_id)
        return game

    async def broadcast_game_state(self, game_id):
        game = self.get_game(game_id)
        if not game:
            return
        state = game.get_state()
        message = {
            'type': 'game_state',
            'game_id': game_id,
            'state': state,
        }
        participants = game.get_participants()
        for username in participants:
            if username == self.node.username:
                continue
            await self.node._send_to_username(username, message)
