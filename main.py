import asyncio
import json
import os
import random
import tempfile
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import chess
import discord
from discord import app_commands
from discord.ext import commands

# ============================================================
# MINI SERVIDOR WEB PARA O RENDER (WEB SERVICE)
# ============================================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Dischess Hub is online!")
        
    def log_message(self, format, *args):
        # Silencia os logs HTTP para não sujar a consola do bot
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# Inicia o servidor web em segundo plano para o Render não dar Timeout
server_thread = threading.Thread(target=run_server)
server_thread.daemon = True
server_thread.start()


# ============================================================
# CONFIGURAÇÃO DO BOT
# ============================================================
intents = discord.Intents.default()
intents.members = True  # Necessário para atualizar cargos de ELO.
intents.message_content = True  # Necessário para comandos e mensagens.

bot = commands.Bot(command_prefix="!", intents=intents)

ELO_INICIAL = 100
ARQUIVO_ELO = Path(os.getenv("ELO_FILE", "elo_data.json"))

ELO_ROLES = [
    (2000, "Grão-Mestre"),
    (1500, "Mestre"),
    (1000, "Avançado"),
    (500, "Intermediário"),
    (0, "Iniciante"),
]

partidas_por_usuario: dict[int, "ChessGameView"] = {}
desafios_pendentes: set[tuple[int, int]] = set()


# ============================================================
# PERSISTÊNCIA DO ELO
# ============================================================
def carregar_elos() -> dict[int, int]:
    if not ARQUIVO_ELO.exists():
        return {}

    try:
        with ARQUIVO_ELO.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        if not isinstance(dados, dict):
            raise ValueError("O ficheiro de ELO precisa conter um objeto JSON.")

        elos: dict[int, int] = {}
        for user_id, valor in dados.items():
            try:
                elo = int(valor)
                if elo < 0:
                    continue
                elos[int(user_id)] = elo
            except (TypeError, ValueError):
                continue
        return elos

    except (OSError, json.JSONDecodeError, ValueError) as erro:
        print(f"ERRO ao carregar {ARQUIVO_ELO}: {erro}")
        return {}


user_elo: dict[int, int] = carregar_elos()


def salvar_elos() -> None:
    ARQUIVO_ELO.parent.mkdir(parents=True, exist_ok=True)
    dados = {str(user_id): elo for user_id, elo in user_elo.items()}
    temporario = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(ARQUIVO_ELO.parent),
            delete=False,
            suffix=".tmp",
        ) as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=2)
            arquivo.write("\n")
            temporario = arquivo.name

        os.replace(temporario, ARQUIVO_ELO)
    except OSError as erro:
        print(f"ERRO ao salvar ELO em {ARQUIVO_ELO}: {erro}")
        if temporario and os.path.exists(temporario):
            try:
                os.remove(temporario)
            except OSError:
                pass


def get_elo(user_id: int) -> int:
    return user_elo.get(user_id, ELO_INICIAL)


def alterar_elo(user_id: int, delta: int) -> int:
    user_elo[user_id] = max(0, get_elo(user_id) + delta)
    salvar_elos()
    return user_elo[user_id]


# ============================================================
# CARGOS DE ELO
# ============================================================
async def atualizar_cargo_elo(
    guild: discord.Guild, member: discord.Member, novo_elo: int
) -> None:
    nome_alvo = "Iniciante"
    for elo_minimo, nome_cargo in ELO_ROLES:
        if novo_elo >= elo_minimo:
            nome_alvo = nome_cargo
            break

    nomes_cargos_elo = {nome for _, nome in ELO_ROLES}
    cargo_alvo = discord.utils.get(guild.roles, name=nome_alvo)

    if cargo_alvo is None:
        return

    remover = [
        cargo
        for cargo in member.roles
        if cargo.name in nomes_cargos_elo and cargo.id != cargo_alvo.id
    ]

    try:
        if remover:
            await member.remove_roles(*remover, reason="Atualização do cargo de ELO")
        if cargo_alvo not in member.roles:
            await member.add_roles(cargo_alvo, reason="Atualização do cargo de ELO")
    except discord.HTTPException:
        pass


async def atualizar_cargos_dos_jogadores(
    guild: discord.Guild, membros: list[discord.Member]
) -> None:
    for membro in membros:
        await atualizar_cargo_elo(guild, membro, get_elo(membro.id))


# ============================================================
# REPRESENTAÇÃO DO TABULEIRO
# ============================================================
PECAS = {
    chess.Piece(chess.PAWN, chess.WHITE): "♙",
    chess.Piece(chess.KNIGHT, chess.WHITE): "♘",
    chess.Piece(chess.BISHOP, chess.WHITE): "♗",
    chess.Piece(chess.ROOK, chess.WHITE): "♖",
    chess.Piece(chess.QUEEN, chess.WHITE): "♕",
    chess.Piece(chess.KING, chess.WHITE): "♔",
    chess.Piece(chess.PAWN, chess.BLACK): "♟",
    chess.Piece(chess.KNIGHT, chess.BLACK): "♞",
    chess.Piece(chess.BISHOP, chess.BLACK): "♝",
    chess.Piece(chess.ROOK, chess.BLACK): "♜",
    chess.Piece(chess.QUEEN, chess.BLACK): "♛",
    chess.Piece(chess.KING, chess.BLACK): "♚",
}
CASA_VAZIA = "·"
NOME_PROMOCAO = {
    chess.QUEEN: "dama",
    chess.ROOK: "torre",
    chess.BISHOP: "bispo",
    chess.KNIGHT: "cavalo",
}


def representar_tabuleiro(board: chess.Board) -> str:
    linhas = ["```"]
    linhas.append("    a b c d e f g h")
    for rank in range(7, -1, -1):
        casas = []
        for file_index in range(8):
            peca = board.piece_at(chess.square(file_index, rank))
            casas.append(PECAS.get(peca, CASA_VAZIA) if peca else CASA_VAZIA)
        linhas.append(f" {rank + 1}  " + " ".join(casas) + f"  {rank + 1}")
    linhas.append("    a b c d e f g h")
    linhas.append("```")
    return "\n".join(linhas)


def texto_status_partida(game: "ChessGameView", prefixo: str | None = None) -> str:
    if game.encerrada:
        status = prefixo or "🏁 **Partida encerrada.**"
    else:
        turno = game.jogador_da_vez()
        status = (
            f"♟️ **Partida em andamento**\n"
            f"⚪ **Brancas:** {game.brancas.mention} — {get_elo(game.brancas.id)} ELO\n"
            f"⚫ **Pretas:** {game.pretas.mention} — {get_elo(game.pretas.id)} ELO\n"
            f"➡️ **Vez de:** {turno.mention}"
        )
        if game.proposta_empate_por is not None:
            status += (
                f"\n🤝 Empate proposto por {game.proposta_empate_por.mention}. "
                "O adversário pode aceitar pelo botão de empate."
            )

    if game.casa_selecionada is not None and not game.encerrada:
        status += f"\n📍 Peça selecionada: **{chess.square_name(game.casa_selecionada)}**"

    return status + "\n\n" + representar_tabuleiro(game.board)


# ============================================================
# CONTROLES DINÂMICOS DO TABULEIRO
# ============================================================
class SelecionarPecaButton(discord.ui.Button):
    def __init__(self, game: "ChessGameView", square: int, piece: chess.Piece, row: int):
        label = f"{PECAS[piece]} {chess.square_name(square)}"
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.game = game
        self.square = square

    async def callback(self, interaction: discord.Interaction):
        game = self.game
        if game.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id != game.jogador_da_vez().id:
            await interaction.response.send_message("Não é o seu turno!", ephemeral=True)
            return

        self.game.casa_selecionada = self.square
        self.game.pagina_destinos = 0
        if not any(move.from_square == self.square for move in game.board.legal_moves):
            self.game.casa_selecionada = None
            self.game.reconstruir_componentes()
            await interaction.response.edit_message(
                content=texto_status_partida(game) + "\n⚠️ Essa peça não tem movimentos legais agora.",
                view=game,
            )
            return

        self.game.reconstruir_componentes()
        await interaction.response.edit_message(
            content=texto_status_partida(game) + "\nEscolha uma casa de destino nos botões.",
            view=game,
        )


class SelecionarDestinoButton(discord.ui.Button):
    def __init__(self, game: "ChessGameView", move: chess.Move, row: int):
        label = chess.square_name(move.to_square)
        if move.promotion:
            label += f" = {NOME_PROMOCAO[move.promotion]}"
        super().__init__(label=label[:80], style=discord.ButtonStyle.success, row=row)
        self.game = game
        self.uci = move.uci()

    async def callback(self, interaction: discord.Interaction):
        await self.game.executar_jogada(interaction, self.uci)


class PaginaAnterior(discord.ui.Button):
    def __init__(self, game: "ChessGameView"):
        super().__init__(label="◀ Destinos", style=discord.ButtonStyle.secondary, row=3)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        if self.game.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id != self.game.jogador_da_vez().id:
            await interaction.response.send_message("Não é o seu turno!", ephemeral=True)
            return
        self.game.pagina_destinos = max(0, self.game.pagina_destinos - 1)
        self.game.reconstruir_componentes()
        await interaction.response.edit_message(
            content=texto_status_partida(self.game) + "\nEscolha uma casa de destino nos botões.",
            view=self.game,
        )


class ProximaPagina(discord.ui.Button):
    def __init__(self, game: "ChessGameView"):
        super().__init__(label="Destinos ▶", style=discord.ButtonStyle.secondary, row=3)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        if self.game.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id != self.game.jogador_da_vez().id:
            await interaction.response.send_message("Não é o seu turno!", ephemeral=True)
            return
        self.game.pagina_destinos += 1
        self.game.reconstruir_componentes()
        await interaction.response.edit_message(
            content=texto_status_partida(self.game) + "\nEscolha uma casa de destino nos botões.",
            view=self.game,
        )


class VoltarSelecao(discord.ui.Button):
    def __init__(self, game: "ChessGameView"):
        super().__init__(label="Escolher outra peça", style=discord.ButtonStyle.secondary, row=4)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        if self.game.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id != self.game.jogador_da_vez().id:
            await interaction.response.send_message("Não é o seu turno!", ephemeral=True)
            return
        self.game.casa_selecionada = None
        self.game.pagina_destinos = 0
        self.game.reconstruir_componentes()
        await interaction.response.edit_message(content=texto_status_partida(self.game), view=self.game)


class ProporEmpate(discord.ui.Button):
    def __init__(self, game: "ChessGameView"):
        super().__init__(
            label="Propor / aceitar empate",
            style=discord.ButtonStyle.secondary,
            emoji="🤝",
            row=4,
        )
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        game = self.game
        if game.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id not in (game.brancas.id, game.pretas.id):
            await interaction.response.send_message("Você não participa desta partida.", ephemeral=True)
            return

        if game.proposta_empate_por is not None:
            if game.proposta_empate_por.id == interaction.user.id:
                await interaction.response.send_message(
                    "Você já propôs o empate. Aguarde a resposta do adversário.",
                    ephemeral=True,
                )
                return

            await game.encerrar_empate(interaction)
            return

        game.proposta_empate_por = interaction.user
        game.reconstruir_componentes()
        await interaction.response.edit_message(
            content=texto_status_partida(game)
            + f"\n🤝 {interaction.user.mention} propôs empate. "
              f"{game.oponente_de(interaction.user).mention}, clique em aceitar empate.",
            view=game,
        )


class Desistir(discord.ui.Button):
    def __init__(self, game: "ChessGameView"):
        super().__init__(
            label="Desistir / render-se",
            style=discord.ButtonStyle.danger,
            emoji="🏳️",
            row=4,
        )
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        game = self.game
        if game.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id not in (game.brancas.id, game.pretas.id):
            await interaction.response.send_message("Você não participa desta partida.", ephemeral=True)
            return

        vencedor = game.oponente_de(interaction.user)
        await game.encerrar_vitoria(
            interaction,
            vencedor=vencedor,
            derrotado=interaction.user,
            motivo=f"🏳️ **{interaction.user.mention} desistiu da partida!**",
        )


class ChessGameView(discord.ui.View):
    def __init__(self, brancas: discord.Member, pretas: discord.Member):
        super().__init__(timeout=None)
        self.brancas = brancas
        self.pretas = pretas
        self.board = chess.Board()
        self.casa_selecionada: int | None = None
        self.pagina_destinos = 0
        self.proposta_empate_por: discord.abc.User | None = None
        self.encerrada = False
        self.reconstruir_componentes()

    def jogador_da_vez(self) -> discord.Member:
        return self.brancas if self.board.turn == chess.WHITE else self.pretas

    def oponente_de(self, usuario: discord.abc.User) -> discord.Member:
        return self.pretas if usuario.id == self.brancas.id else self.brancas

    def reconstruir_componentes(self) -> None:
        self.clear_items()
        if self.encerrada:
            return

        if self.casa_selecionada is None:
            pecas_do_turno = [
                (square, piece)
                for square, piece in self.board.piece_map().items()
                if piece.color == self.board.turn
            ]
            pecas_do_turno.sort(key=lambda item: item[0])
            for index, (square, piece) in enumerate(pecas_do_turno):
                self.add_item(SelecionarPecaButton(self, square, piece, row=index // 4))
        else:
            legais = sorted(
                [move for move in self.board.legal_moves if move.from_square == self.casa_selecionada],
                key=lambda move: (move.to_square, move.promotion or 0),
            )
            if not legais:
                self.casa_selecionada = None
                self.reconstruir_componentes()
                return

            total_paginas = max(1, (len(legais) + 14) // 15)
            self.pagina_destinos = min(self.pagina_destinos, total_paginas - 1)
            inicio = self.pagina_destinos * 15
            pagina = legais[inicio : inicio + 15]
            for index, move in enumerate(pagina):
                self.add_item(SelecionarDestinoButton(self, move, row=index // 5))

            if self.pagina_destinos > 0:
                self.add_item(PaginaAnterior(self))
            if self.pagina_destinos < total_paginas - 1:
                self.add_item(ProximaPagina(self))
            self.add_item(VoltarSelecao(self))

        self.add_item(ProporEmpate(self))
        self.add_item(Desistir(self))

    async def executar_jogada(self, interaction: discord.Interaction, uci: str) -> None:
        if self.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        if interaction.user.id != self.jogador_da_vez().id:
            await interaction.response.send_message("Não é o seu turno!", ephemeral=True)
            return

        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            await interaction.response.send_message("Movimento inválido.", ephemeral=True)
            return

        if move not in self.board.legal_moves:
            await interaction.response.send_message(
                "Esse movimento não é legal nesta posição. Selecione a peça novamente.",
                ephemeral=True,
            )
            return

        self.board.push(move)
        self.casa_selecionada = None
        self.pagina_destinos = 0
        self.proposta_empate_por = None

        resultado = self.board.outcome(claim_draw=True)
        if resultado is not None:
            if resultado.winner is None:
                await self.encerrar_empate_automatico(interaction, resultado.termination.name)
            else:
                vencedor = self.brancas if resultado.winner == chess.WHITE else self.pretas
                derrotado = self.oponente_de(vencedor)
                motivo = (
                    f"👑 **XEQUE-MATE! {vencedor.mention} venceu a partida!**"
                    if self.board.is_checkmate()
                    else f"🏁 **Partida encerrada: {resultado.termination.name.replace('_', ' ').title()}**\n"
                         f"🏆 Vencedor: {vencedor.mention}"
                )
                await self.encerrar_vitoria(
                    interaction, vencedor, derrotado, motivo=motivo
                )
            return

        self.reconstruir_componentes()
        aviso = "\n⚠️ **Xeque!**" if self.board.is_check() else ""
        await interaction.response.edit_message(
            content=texto_status_partida(self) + aviso,
            view=self,
        )

    async def encerrar_vitoria(
        self,
        interaction: discord.Interaction,
        vencedor: discord.Member,
        derrotado: discord.Member,
        motivo: str,
    ) -> None:
        if self.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return

        self.encerrada = True
        ganho = random.randint(12, 32)
        perda = random.randint(10, 30)
        elo_vencedor = alterar_elo(vencedor.id, ganho)
        elo_derrotado = alterar_elo(derrotado.id, -perda)
        self.remover_partida_ativa()
        self.clear_items()

        conteudo = (
            f"{motivo}\n"
            f"🏆 **Vencedor:** {vencedor.mention} (+{ganho} ELO | Total: {elo_vencedor})\n"
            f"💔 **Derrotado:** {derrotado.mention} (-{perda} ELO | Total: {elo_derrotado})\n\n"
            + representar_tabuleiro(self.board)
        )
        await interaction.response.edit_message(content=conteudo, view=self)
        if interaction.guild:
            await atualizar_cargos_dos_jogadores(interaction.guild, [vencedor, derrotado])

    async def encerrar_empate(self, interaction: discord.Interaction) -> None:
        if self.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        self.encerrada = True
        elo_brancas = alterar_elo(self.brancas.id, -5)
        elo_pretas = alterar_elo(self.pretas.id, -5)
        self.remover_partida_ativa()
        self.clear_items()

        await interaction.response.edit_message(
            content=(
                "🤝 **Fim de jogo! Empate por acordo!**\n"
                "⚖️ Ambos perderam 5 ELO.\n"
                f"⚪ **{self.brancas.mention}:** {elo_brancas} ELO\n"
                f"⚫ **{self.pretas.mention}:** {elo_pretas} ELO\n\n"
                + representar_tabuleiro(self.board)
            ),
            view=self,
        )
        if interaction.guild:
            await atualizar_cargos_dos_jogadores(
                interaction.guild, [self.brancas, self.pretas]
            )

    async def encerrar_empate_automatico(
        self, interaction: discord.Interaction, motivo: str
    ) -> None:
        if self.encerrada:
            await interaction.response.send_message("Esta partida já terminou.", ephemeral=True)
            return
        self.encerrada = True
        elo_brancas = alterar_elo(self.brancas.id, -5)
        elo_pretas = alterar_elo(self.pretas.id, -5)
        self.remover_partida_ativa()
        self.clear_items()

        await interaction.response.edit_message(
            content=(
                f"🤝 **Partida empatada automaticamente!**\n"
                f"Motivo: `{motivo.replace('_', ' ').title()}`\n"
                "⚖️ Ambos perderam 5 ELO.\n"
                f"⚪ **{self.brancas.mention}:** {elo_brancas} ELO\n"
                f"⚫ **{self.pretas.mention}:** {elo_pretas} ELO\n\n"
                + representar_tabuleiro(self.board)
            ),
            view=self,
        )
        if interaction.guild:
            await atualizar_cargos_dos_jogadores(
                interaction.guild, [self.brancas, self.pretas]
            )

    def remover_partida_ativa(self) -> None:
        for user_id in (self.brancas.id, self.pretas.id):
            if partidas_por_usuario.get(user_id) is self:
                partidas_por_usuario.pop(user_id, None)


# ============================================================
# ACEITAR / RECUSAR DESAFIO
# ============================================================
class DesafioView(discord.ui.View):
    def __init__(self, desafiante: discord.Member, oponente: discord.Member):
        super().__init__(timeout=120)
        self.desafiante = desafiante
        self.oponente = oponente
        self.mensagem: discord.Message | None = None
        self.resolvido = False

    async def on_timeout(self) -> None:
        if self.resolvido:
            return
        self.resolvido = True
        desafios_pendentes.discard((self.desafiante.id, self.oponente.id))
        for item in self.children:
            item.disabled = True
        if self.mensagem:
            try:
                await self.mensagem.edit(
                    content=(
                        f"⌛ O desafio de {self.desafiante.mention} para "
                        f"{self.oponente.mention} expirou."
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(
        label="Aceitar desafio", style=discord.ButtonStyle.success, emoji="✅"
    )
    async def aceitar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.oponente.id:
            await interaction.response.send_message(
                "Este desafio não é para você.", ephemeral=True
            )
            return
        if self.resolvido:
            await interaction.response.send_message(
                "Este desafio já foi resolvido ou expirou.", ephemeral=True
            )
            return

        envolvidos = (self.desafiante.id, self.oponente.id)
        ocupados = [uid for uid in envolvidos if uid in partidas_por_usuario]
        if ocupados:
            self.resolvido = True
            desafios_pendentes.discard(envolvidos)
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                content="❌ Um dos jogadores já está em outra partida. Desafio cancelado.",
                view=self,
            )
            return

        self.resolvido = True
        desafios_pendentes.discard(envolvidos)
        game = ChessGameView(self.desafiante, self.oponente)
        partidas_por_usuario[self.desafiante.id] = game
        partidas_por_usuario[self.oponente.id] = game

        for item in self.children:
            item.disabled = True
        game.reconstruir_componentes()

        await interaction.response.edit_message(
            content=texto_status_partida(game),
            view=game,
        )

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger, emoji="❌")
    async def recusar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.oponente.id:
            await interaction.response.send_message(
                "Este desafio não é para você.", ephemeral=True
            )
            return
        if self.resolvido:
            await interaction.response.send_message(
                "Este desafio já foi resolvido ou expirou.", ephemeral=True
            )
            return

        self.resolvido = True
        desafios_pendentes.discard((self.desafiante.id, self.oponente.id))
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=(
                f"❌ {self.oponente.mention} recusou o desafio de "
                f"{self.desafiante.mention}."
            ),
            view=self,
        )


# ============================================================
# COMANDOS DO BOT
# ============================================================
@bot.event
async def on_ready():
    print(f"Bot online como: {bot.user} (ID: {bot.user.id})")
    try:
        sincronizados = await bot.tree.sync()
        print(f"Sincronizados {len(sincronizados)} comandos de barra.")
    except discord.HTTPException as erro:
        print(f"Erro ao sincronizar comandos: {erro}")


@bot.tree.command(
    name="desafiar",
    description="Desafie um amigo para uma partida de xadrez no Dischess Hub.",
)
@app_commands.describe(oponente="Selecione o jogador que deseja desafiar")
async def desafiar(interaction: discord.Interaction, oponente: discord.Member):
    desafiante = interaction.user

    if not isinstance(desafiante, discord.Member) or interaction.guild is None:
        await interaction.response.send_message(
            "Use este comando dentro de um servidor.", ephemeral=True
        )
        return
    if oponente.id == desafiante.id:
        await interaction.response.send_message(
            "❌ Você não pode desafiar a si mesmo!", ephemeral=True
        )
        return
    if oponente.bot:
        await interaction.response.send_message(
            "❌ Você não pode desafiar um bot!", ephemeral=True
        )
        return
    if desafiante.id in partidas_por_usuario or oponente.id in partidas_por_usuario:
        await interaction.response.send_message(
            "❌ Você ou esse jogador já está em uma partida.", ephemeral=True
        )
        return

    chave = (desafiante.id, oponente.id)
    if chave in desafios_pendentes or (oponente.id, desafiante.id) in desafios_pendentes:
        await interaction.response.send_message(
            "Já existe um desafio pendente entre vocês.", ephemeral=True
        )
        return

    desafios_pendentes.add(chave)
    view = DesafioView(desafiante, oponente)
    await interaction.response.send_message(
        f"♟️ **Desafio de xadrez — Dischess Hub**\n"
        f"{oponente.mention}, você foi desafiado por {desafiante.mention}!\n"
        "Você tem 120 segundos para aceitar ou recusar.",
        view=view,
    )
    try:
        view.mensagem = await interaction.original_response()
    except discord.HTTPException:
        pass


@bot.tree.command(name="elo", description="Veja o seu ELO ou o de outro jogador.")
@app_commands.describe(usuario="Usuário para consultar o ELO")
async def consultar_elo(
    interaction: discord.Interaction, usuario: discord.Member | None = None
):
    alvo = usuario or interaction.user
    await interaction.response.send_message(
        f"📊 O ELO de {alvo.mention} é **{get_elo(alvo.id)}**."
    )


# ============================================================
# INICIALIZAÇÃO
# ============================================================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Token não encontrado. Configure a variável de ambiente DISCORD_TOKEN."
    )

bot.run(TOKEN)
