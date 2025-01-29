import discord
from discord import app_commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv
import os
import openai
import requests
from datetime import datetime
import random
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

# Bot inicializálása
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# WAV fájl mentési mappa
MUSIC_FOLDER = "MUSIC"

# YouTube DL beállítások
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,  # Playlist támogatás
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch1',  # Cím alapján keresés
    'source_address': '0.0.0.0',  # IPv4 preferálása
    'http_chunk_size': 1048576  # Adat chunk méret növelése
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)
queue = []

log_file = "music_bot_log.txt"

#spoti decl test
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    print("❌ Nincsenek beállítva a Spotify API kulcsok!")
    exit()

spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID, 
    client_secret=SPOTIFY_CLIENT_SECRET
))

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            return [cls(discord.FFmpegPCMAudio(entry['url'], **ffmpeg_options), data=entry) for entry in data['entries']]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

@bot.event
async def on_ready():
    print(f"Bejelentkezve mint {bot.user}")
    try:
        synced = await tree.sync()
        print(f"Sikeresen szinkronizált parancsok: {len(synced)}")
        for command in synced:
            print(f"Parancs neve: {command.name}")
    except Exception as e:
        print(f"Hiba a parancsok szinkronizálásakor: {e}")

# /join parancs
@tree.command(name="join", description="Meghívja a botot a voice channelbe")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("Nem vagy voice channelben!", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    if interaction.guild.voice_client:
        await interaction.response.send_message("Már csatlakozva vagyok egy voice channelhez.", ephemeral=True)
        return
    await channel.connect()
    await interaction.response.send_message("Csatlakoztam a voice channelhez.")

# restartt stteam
async def restart_stream(interaction, track_url):
    """Újraindítja a streamet, ha megszakad."""
    try:
        players = await YTDLSource.from_url(track_url, loop=bot.loop, stream=True)
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.user.voice.channel.connect()
            voice_client = interaction.guild.voice_client

        def after_playing(e):
            if e:
                print(f"Hiba a lejátszás közben: {e}")
                asyncio.run_coroutine_threadsafe(restart_stream(interaction, track_url), bot.loop)

        voice_client.play(players, after=after_playing)
        await interaction.channel.send(f"A zene újraindult: {players.title}")
    except Exception as e:
        print(f"Hiba az újraindítás során: {e}")
        await interaction.channel.send("Nem sikerült újraindítani a zenét.")
#convert
def convert_spotify_to_youtube(spotify_url):
    """Átalakítja a Spotify URL-t egy YouTube keresési lekérdezéssé."""
    try:
        track_id = spotify_url.split("/")[-1].split("?")[0]
        track_info = spotify.track(track_id)
        
        if not track_info:
            print("❌ A Spotify API nem adott vissza adatot!")
            return None
        
        track_name = track_info['name']
        artist_name = track_info['artists'][0]['name']
        search_query = f"ytsearch1:{track_name} {artist_name}"

        print(f"🎵 Spotify konvertálva: {search_query}")  # Debug log
        
        return search_query
    except Exception as e:
        print(f"❌ Hiba történt a Spotify feldolgozása során: {e}")
        return None

# /play parancs
@tree.command(name="play", description="Lejátszik egy számot, Spotify URL-t vagy YouTube keresést")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.response.send_message("Először csatlakozz egy voice channelhez!", ephemeral=True)
        return

    voice_client = interaction.guild.voice_client
    if not voice_client:
        await interaction.user.voice.channel.connect()
        voice_client = interaction.guild.voice_client

    await interaction.response.defer()  # Jelezzük, hogy dolgozunk a válaszon

    # Ha Spotify URL-t kaptunk, konvertáljuk YouTube kereséssé
    if "open.spotify.com/track" in search:
        search = convert_spotify_to_youtube(search)
        if not search:
            await interaction.followup.send("Nem sikerült feldolgozni a Spotify linket.")
            return

    try:
        players = await YTDLSource.from_url(search, loop=bot.loop, stream=True)

        if isinstance(players, list):
            queue.extend(players)
            if not voice_client.is_playing():
                def after_playing(e):
                    if e:
                        print(f"Hiba a lejátszás közben: {e}")
                        asyncio.run_coroutine_threadsafe(restart_stream(interaction, search), bot.loop)

                    asyncio.run_coroutine_threadsafe(check_queue(interaction), bot.loop)

                voice_client.play(queue.pop(0), after=after_playing)
            await interaction.followup.send(f"Hozzáadva a várólistához: {players[0].title}")
        else:
            queue.append(players)
            if not voice_client.is_playing():
                def after_playing(e):
                    if e:
                        print(f"Hiba a lejátszás közben: {e}")
                        asyncio.run_coroutine_threadsafe(restart_stream(interaction, search), bot.loop)

                    asyncio.run_coroutine_threadsafe(check_queue(interaction), bot.loop)

                voice_client.play(queue.pop(0), after=after_playing)
                await interaction.followup.send(f"Most játszom: {players.title}")
            else:
                await interaction.followup.send(f"Hozzáadva a várólistához: {players.title}")
    except Exception as e:
        await interaction.followup.send("Hiba történt a szám betöltésekor.")
        print(e)
        
# queckecker
async def check_queue(interaction):
    voice_client = interaction.guild.voice_client
    if queue and not voice_client.is_playing():
        next_song = queue.pop(0)
        def after_playing(e):
            if e:
                print(f"Hiba a lejátszás közben: {e}")
                asyncio.run_coroutine_threadsafe(restart_stream(interaction, next_song.data['url']), bot.loop)

            asyncio.run_coroutine_threadsafe(check_queue(interaction), bot.loop)

        voice_client.play(next_song, after=after_playing)
        await interaction.channel.send(f"Most játszom: {next_song.title}")

#logolás lenne de szar      
def log_song(user, title, queued=False):
    """ Logolja a számot, a kérő nevét és az időpontot. """
    action = "Várólistához adva" if queued else "Lejátszva"
    try:
        with open(log_file, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {user} - {action}: {title}\n")
    except Exception as e:
        print(f"Nem sikerült logolni: {e}")


# /skip parancs
@tree.command(name="skip", description="Átugorja az aktuális számot")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    await interaction.response.defer()  # Jelezzük, hogy dolgozunk a válaszon
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await interaction.followup.send("A szám átugrásra került.")
    else:
        await interaction.followup.send("Nincs szám lejátszásban, amit át lehetne ugrani.")


# /stop parancs
@tree.command(name="stop", description="Leállítja a zenét és törli a várólistát")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    await interaction.response.defer()  # Jelezzük, hogy dolgozunk a válaszon
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        queue.clear()
        await interaction.followup.send("A zene lejátszása leállítva, és a várólista törölve.")
    else:
        await interaction.followup.send("Nincs zene a lejátszásban.")
# remove parancs
@tree.command(name="remove", description="Eltávolít egy dalt a várólistából a pozíció alapján")
async def remove(interaction: discord.Interaction, position: int):
    await interaction.response.defer()
    if position < 1 or position > len(queue):
        await interaction.followup.send("Érvénytelen pozíció!", ephemeral=True)
        return

    removed_song = queue.pop(position - 1)
    await interaction.followup.send(f"Eltávolítva a várólistából: {removed_song.title}")
    
# egész listát törli
@tree.command(name="clear", description="Törli a várólistát")
async def clear(interaction: discord.Interaction):
    await interaction.response.defer()
    queue.clear()
    await interaction.followup.send("A várólista törölve!")
    
#segitseg
@tree.command(name="help", description="Felsorolja az elérhető parancsokat")
async def help_command(interaction: discord.Interaction):
    commands = [
        {"name": "/play <keresés>", "description": "Lejátszik egy számot, Spotify URL-t vagy YouTube playlistet."},
        {"name": "/remove <pozíció>", "description": "Eltávolít egy dalt a várólistából a pozíció alapján."},
        {"name": "/clear", "description": "Törli a várólistát."},
        {"name": "/join", "description": "Meghívja a botot a voice channelbe."},
        {"name": "/skip", "description": "Átugorja az aktuális számot."},
        {"name": "/stop", "description": "Leállítja a zenét és törli a várólistát."},
        {"name": "/help", "description": "Felsorolja az elérhető parancsokat."}
    ]

    help_message = "Elérhető parancsok:\n"
    for command in commands:
        help_message += f"**{command['name']}** - {command['description']}\n"

    await interaction.response.send_message(help_message)        
        
# /motivate motiválo game
@tree.command(name="motivate", description="Küld egy motivációs üzenetet.")
async def motivate(interaction: discord.Interaction):
    await interaction.response.defer()  # Jelezd, hogy dolgozol
    try:
        quotes = [
    "Ne feledd, a nap mindig süt valahol!",
    "Ma egy új lehetőség, hogy jobb legyél!",
    "A kudarc az első lépés a sikerhez!",
    "Nevess sokat, az ingyen van!",
    "Ne aggódj, a bot mindig itt van neked!",
    "A mosolyod a legjobb ékszered!",
    "A változás a növekedés kulcsa.",
    "Minden nap új esélyt hoz!",
    "Higgy magadban, mert senki más nem fog helyetted.",
    "Soha ne add fel, mert a siker közel lehet!",
    "Minden küzdelem erősebbé tesz.",
    "A nehézségek csak átmeneti állomások.",
    "A boldogság nem cél, hanem út.",
    "Törekedj arra, hogy a mai nap jobb legyen, mint a tegnap.",
    "A legnagyobb erősség az, ha kitartasz.",
    "Minden álom megvalósítható, ha dolgozol érte.",
    "A kreativitás a siker egyik titka.",
    "A legjobb idő elkezdeni valamit az most!",
    "A belső béke a legnagyobb ajándék.",
    "Az élet túl rövid ahhoz, hogy aggódj.",
    "Tudd, hogy képes vagy többre, mint amit gondolsz!",
    "Bízz az utadban, még ha nem is látod a végét.",
    "A mosolyod valaki napját szebbé teheti.",
    "A siker nem végleges, a kudarc nem végzetes: csak a bátorság számít.",
    "Az akadályok csak kihívások, amiket meg kell oldani.",
    "A pozitív gondolkodás csodákat tehet.",
    "Légy hálás minden apróságért az életben.",
    "A kudarc tanít meg arra, hogyan legyél sikeres.",
    "A boldogság döntés kérdése.",
    "Minden nap egy új lehetőség arra, hogy fejlődj.",
    "Az élet színes, ha úgy döntesz, hogy azzá teszed.",
    "A legjobb tanulás a tapasztalatból származik.",
    "Minden utazás egy lépéssel kezdődik.",
    "A bátorság az, amikor folytatod akkor is, ha félsz.",
    "Légy büszke arra, amit eddig elértél.",
    "A legnagyobb kihívás magadat legyőzni.",
    "Az optimizmus a kulcs a boldogsághoz.",
    "A nevetés a legjobb orvosság.",
    "Minden pillanat egy új kezdet.",
    "A kis lépések is számítanak.",
    "A türelem rózsát terem.",
    "A sikert nem mérik az idővel, hanem a kitartással.",
    "Az élet egy kaland, élvezd ki minden pillanatát!",
    "Higgy az álmaidban, és valósítsd meg őket!",
    "A szeretet a legnagyobb erő.",
    "Mindig van remény, még a legsötétebb napokon is.",
    "A kihívások teszik az életet izgalmassá.",
    "A jövő azoké, akik hisznek benne.",
    "A kitartás a kulcs minden sikerhez.",
    "Az igazi boldogság belülről fakad.",
    "A legjobb dolgok a várakozáson túl történnek.",
    "Tarts ki, a legjobb még hátravan!",
    "Az élet szép, ha tudsz hinni benne.",
    "A pozitív gondolkodás a siker alapja.",
    "A szeretet mindig visszatér hozzád.",
    "Minden nehézség egy új lehetőség.",
    "Légy önmagad legjobb verziója.",
    "A nevetés a boldogság kulcsa.",
    "A lehetőségek ott vannak mindenhol.",
    "Minden nap egy új fejezet az életedben.",
    "Légy az a változás, amit látni akarsz a világban.",
    "A legnagyobb bátorság az, ha önmagad vagy.",
    "A szeretet mindent legyőz.",
    "A boldogság az apró dolgokban rejlik.",
    "Az élet rövid, élvezd ki minden percét.",
    "A hit hegyeket mozgat.",
    "A szeretet mindig győz.",
    "Tedd szebbé valaki napját!",
    "A legjobb idő a változásra az most van.",
    "Higgy magadban, és bármit elérhetsz.",
    "Minden nagy dolog kis lépésekkel kezdődik."
]
        quote = random.choice(quotes)
        await interaction.followup.send(quote)
    except Exception as e:
        await interaction.followup.send("Hiba történt a motivációs üzenet küldése közben.")
        print(e)

   
   
# /trivia kérdések  
trivia_questions = {
    "Mi Magyarország fővárosa?": "Budapest",
    "Hány lába van egy póknak?": "8",
    "Ki írta a 'Harry Potter' sorozatot?": "J.K. Rowling",
    "Melyik bolygó a Naprendszer legnagyobb bolygója?": "Jupiter",
    "Melyik évben történt a holdraszállás?": "1969",
    "Melyik ország híres a pizzáról?": "Olaszország",
    "Hány perc egy óra?": "60",
    "Melyik kontinens legnagyobb?": "Ázsia",
    "Hány szín van a szivárványban?": "7",
    "Melyik állat ismert hosszú nyakáról?": "Zsiráf",
    "Mi a víz vegyjele?": "H2O",
    "Melyik országban van a Taj Mahal?": "India",
    "Hány lába van egy polipnak?": "8",
    "Melyik sporthoz tartozik a Wimbledon?": "Tenisz",
    "Mi az emberi test legnagyobb szerve?": "Bőr",
    "Ki festette a Mona Lisát?": "Leonardo da Vinci",
    "Hány csillag van az amerikai zászlón?": "50",
    "Mi a Föld legmélyebb óceánja?": "Csendes-óceán",
    "Melyik országban van az Eiffel-torony?": "Franciaország",
    "Melyik bolygót nevezik a Vörös Bolygónak?": "Mars",
    "Milyen színű a zebra csíkja?": "Fekete-fehér",
    "Hány évszak van egy évben?": "4",
    "Mi a Föld legnagyobb sivataga?": "Szahara",
    "Milyen színű a Nap?": "Sárga",
    "Ki volt Albert Einstein?": "Fizikus",
    "Hány nap van egy évben?": "365",
    "Melyik állat ismert a lassúságáról?": "Lajhár",
    "Melyik évben történt a Titanic elsüllyedése?": "1912",
    "Melyik folyó a leghosszabb a világon?": "Nílus",
    "Melyik országban van a Colosseum?": "Olaszország",
    "Mi az emberi test legkisebb csontja?": "Kengyel",
    "Mi a tej fő alkotóeleme?": "Kalcium",
    "Melyik állat ad tejet?": "Tehén",
    "Melyik sporthoz kell ütő?": "Tenisz",
    "Melyik égitest kering a Föld körül?": "Hold",
    "Milyen állat a Nemo a 'Némó nyomában' című filmben?": "Bohóchal",
    "Mi a Himalája legmagasabb hegye?": "Mount Everest",
    "Melyik állat mondja, hogy 'múú'?": "Tehén",
    "Mi a leggyorsabb szárazföldi állat?": "Gepárd",
    "Hány nap van egy szökőévben?": "366",
    "Melyik ország zászlaja piros-fehér?": "Lengyelország",
    "Melyik bolygó a legközelebb a Naphoz?": "Merkúr"
}

@tree.command(name="trivia", description="Egy trivia kérdés.")
async def trivia(interaction: discord.Interaction):
    await interaction.response.defer()  # Jelezd, hogy dolgozol
    try:
        question, answer = random.choice(list(trivia_questions.items()))
        await interaction.followup.send(f"Kérdés: {question}  (30 másodpercedvan válaszolni)")

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            response = await bot.wait_for("message", check=check, timeout=30.0)
            if response.content.lower() == answer.lower():
                await interaction.channel.send(f"Gratulálok, {response.author.mention}! A válasz helyes: {answer}")
            else:
                await interaction.channel.send(f"Sajnálom, {response.author.mention}, a helyes válasz: {answer}")
        except asyncio.TimeoutError:
            await interaction.channel.send(f"Idő lejárt! A helyes válasz: {answer}")
    except Exception as e:
        await interaction.followup.send("Hiba történt a trivia parancs futtatása közben.")
        print(e)
 
#win command 
@tree.command(name="nyerni_fogunk", description="Megtippeli, hogy nyerni fogunk-e.")
async def nyerni_fogunk(interaction: discord.Interaction):
    await interaction.response.defer()  # Jelezd, hogy dolgozol
    try:
        # Esemény valószínűségek
        choices = [
            ("Igen, biztosan nyerni fogunk!", 65),  # 65% esély
            ("Nem, sajnos nem fogunk nyerni...", 25),  # 25% esély
            ("Uhh, ez annyira nehéz kérdés, én sem tudom megmondani. Mindkét csapat olyan rossz!", 10)  # 10% esély
        ]

        # Választás a valószínűségek alapján
        result = random.choices(
            population=[choice[0] for choice in choices],  # Lehetséges válaszok
            weights=[choice[1] for choice in choices],  # Valószínűségi súlyok
            k=1  # Egyetlen választás
        )[0]

        # Küldjük el a választ
        await interaction.followup.send(result)

    except Exception as e:
        await interaction.followup.send("Hiba történt a válasz elkészítése közben.")
        print(e)
        
#wav lejátszasa:
# Biztosítjuk, hogy a zene mappa létezik
if not os.path.exists(MUSIC_FOLDER):
    os.makedirs(MUSIC_FOLDER)

@tree.command(name="play_wav", description="Lejátszik egy feltöltött WAV fájlt és elmenti a 'zene' mappába.")
async def play_wav(interaction: discord.Interaction, attachment: discord.Attachment):
    if not interaction.user.voice:
        await interaction.response.send_message("Először csatlakozz egy voice channelhez!", ephemeral=True)
        return

    voice_client = interaction.guild.voice_client
    if not voice_client:
        await interaction.user.voice.channel.connect()
        voice_client = interaction.guild.voice_client

    # Ellenőrizzük, hogy a fájl valóban WAV formátumú-e
    if not attachment.filename.endswith(".wav"):
        await interaction.response.send_message("Hiba: Csak WAV fájlokat tudok lejátszani!", ephemeral=True)
        return

    # Fájl mentési útvonal
    sanitized_filename = f"{interaction.user.id}_{attachment.filename}"
    file_path = os.path.join(MUSIC_FOLDER, sanitized_filename)

    try:
        # Fájl letöltése a 'zene' mappába
        await attachment.save(file_path)
        await interaction.response.send_message(f"Fájl mentve és lejátszás indul: `{sanitized_filename}`")

        # Lejátszás FFmpeg segítségével
        source = discord.FFmpegPCMAudio(file_path, executable="ffmpeg")
        if voice_client.is_playing():
            voice_client.stop()  # Ha másik szám megy, azt leállítjuk
        voice_client.play(source)

    except Exception as e:
        await interaction.response.send_message(f"Hiba történt a lejátszás során: {e}")
        print(f"Hiba: {e}")
        
        
#reménytelen de talán
@tree.command(name="chat", description="Egy üzenetet küld a Hugging Face GPT modellnek.")
async def chat(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    try:
        response = chatbot(message, max_length=100, num_return_sequences=1)
        await interaction.followup.send(response[0]['generated_text'])
    except Exception as e:
        print(f"Hiba történt a Hugging Face API-val: {e}")
        await interaction.followup.send("Nem sikerült választ adni a kérdésre.")


#beszedfelismero
async def listen_to_speech(interaction: discord.Interaction):
    try:
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            await interaction.response.send_message("Hallgatlak...")
            audio = recognizer.listen(source, timeout=10)

        # Speech-to-Text konvertálás
        text = recognizer.recognize_google(audio, language="hu-HU")
        await interaction.followup.send(f"Ezt hallottam: {text}")
        
        # OpenAI segítségével válasz
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": text}]
        )
        válasz = response['choices'][0]['message']['content']
        await interaction.followup.send(válasz)
    except sr.UnknownValueError:
        await interaction.followup.send("Nem értettem, mit mondtál.")
    except sr.RequestError as e:
        await interaction.followup.send(f"Hiba történt a Speech-to-Text API-val: {e}")

@tree.command(name="listen", description="Figyeli a beszédet és válaszol.")
async def listen(interaction: discord.Interaction):
    await listen_to_speech(interaction)

bot.run(os.getenv('BOT_TOKEN'))
