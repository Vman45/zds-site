try:
    from zds.article.models import Article, ArticleRead, Reaction
    from zds.tutorial.models import Tutorial, Part, Chapter, Note, TutorialRead
    from zds.tutorial.models import Extract as OldExtract
except ImportError:
    print("The old stack is no more available on your zestedesavoir copy")
    exit()


from zds.forum.models import Topic

from zds.tutorialv2.models.models_database import PublishableContent, ContentReaction, ContentRead
from zds.tutorialv2.models.models_versioned import Extract, Container
from zds.tutorialv2.utils import publish_content
from django.core.management.base import BaseCommand
from django.db import transaction
from zds.gallery.models import Gallery, UserGallery
from zds.utils import slugify
from zds.utils.models import Licence
from datetime import datetime
import shutil
import sys


def export_read_for_note(old_note, new_note, read_class):
    queryset = read_class.objects
    if read_class == ArticleRead:
        queryset = queryset.filter(reaction__pk=old_note.pk)
    else:
        queryset = queryset.filter(note__pk=old_note.pk)
    for read in queryset.all():
        new_read = ContentRead()
        new_read.content = new_note.related_content
        new_read.note = new_note
        new_read.user = read.user
        new_read.save()


def export_comments(reacts, exported, read_class):
    c = 0
    for note in reacts:
        new_reac = ContentReaction()
        new_reac.pubdate = note.pubdate
        new_reac.author = note.author
        c += 1
        new_reac.position = c
        new_reac.related_content = exported

        new_reac.update_content(note.text)
        new_reac.ip_address = note.ip_address
        new_reac.save()
        export_read_for_note(note, new_reac, read_class)
    exported.last_note = new_reac
    exported.save()


def progressbar(it, prefix="", size=60):
    count = len(it)

    def _show(_i):
        x = int(size * _i / count)
        sys.stdout.write("%s[%s%s] %i/%i\r" % (prefix, "#" * x, "." * (size - x), _i, count))
        sys.stdout.flush()

    _show(0)
    for i, item in enumerate(it):
        yield item
        _show(i + 1)
    sys.stdout.write("\n")
    sys.stdout.flush()


def create_gallery_for_article(content):
    # Creating the gallery
    gal = Gallery()
    gal.title = content.title
    gal.slug = slugify(content.title)
    gal.pubdate = datetime.now()
    gal.save()

    # Attach user to gallery
    for user in content.authors.all():
        userg = UserGallery()
        userg.gallery = gal
        userg.mode = "W"  # write mode
        userg.user = user
        userg.save()
    content.gallery = gal


def migrate_articles():
    articles = Article.objects.all()
    if len(articles) == 0:
        return
    for i in progressbar(xrange(len(articles)), "Exporting articles", 100):
        current = articles[i]
        exported = PublishableContent()
        exported.slug = current.slug
        exported.type = "ARTICLE"
        exported.title = current.title
        [exported.authors.add(author) for author in current.authors.all()]
        exported.creation_date = current.create_at
        exported.image = current.image
        exported.description = current.description
        exported.js_support = current.js_support  # todo: check articles have js_support
        create_gallery_for_article(exported)
        # todo: migrate categories !!
        shutil.copytree(current.get_path(False), exported.get_repo_path(False))
        # now, re create the manifest.json
        exported.sha_draft = current.sha_draft
        exported.licence = current.licence
        versioned = exported.load_version()
        article_extract = Extract(current.title, "text", versioned)
        versioned.type = "ARTICLE"

        versioned.licence = exported.licence.title

        versioned.add_extract(article_extract)
        versioned.dump_json()
        exported.sha_draft = versioned.commit_changes(u"Migration version 2")
        exported.old_pk = current.pk
        exported.save()
        # todo  : generate mapping
        # todo: handle notes
        reacts = Reaction.objects.filter(article__pk=current.pk)\
                                 .select_related("author")\
                                 .order_by("pubdate")\
                                 .all()
        export_comments(reacts, exported, ArticleRead)
        # todo: handle publication
        if current.sha_public is not None and current.sha_public != "":
            publish_content(exported, exported.load_version(current.sha_public), False)
            exported.pubdate = current.pudate
            exported.sha_public = current.sha_public
            exported.save()
            exported.public_version.content_public_slug = current.slug
            exported.public_version.publication_date = current.pubdate
            exported.public_version.save()


def migrate_mini_tuto():
    mini_tutos = Tutorial.objects.prefetch_related("licence").filter(type="MINI").all()
    for i in progressbar(xrange(len(mini_tutos)), "Exporting articles", 100):
        current = mini_tutos[i]
        exported = PublishableContent()
        exported.slug = current.slug
        exported.type = "TUTORIAL"
        exported.title = current.title
        exported.sha_draft = current.sha_draft
        exported.licence = Licence.objects.filter(code=current.licence).first()

        exported.creation_date = current.create_at
        exported.image = current.image
        exported.description = current.description
        exported.js_support = current.js_support
        exported.save()
        [exported.subcategory.add(category) for category in current.subcategory.all()]
        [exported.helps.add(help) for help in current.helps.all()]
        [exported.authors.add(author) for author in current.authors.all()]
        shutil.copytree(current.get_path(False), exported.get_repo_path(False))
        # now, re create the manifest.json
        versioned = exported.load_version()
        versioned.licence = exported.licence
        exported.gallery = current.gallery

        versioned.type = "TUTORIAL"

        for extract in OldExtract.objects.filter(chapter=current.get_chapter()):
            minituto_extract = Extract(extract.title, extract.text[:-3].split("/")[-1])
            minituto_extract.text = extract.text
            versioned.add_extract(minituto_extract)
        versioned.dump_json()

        exported.sha_draft = versioned.commit_changes(u"Migration version 2")
        if current.is_beta():
            exported.sha_beta = exported.sha_draft
            exported.beta_topic = Topic.objects.get(key=current.pk).first()

        exported.old_pk = current.pk
        exported.save()
        # export beta forum post
        former_topic = Topic.objet.get(key=current.pk)
        if former_topic is not None:
            former_topic.related_publishable_content = exported
            former_topic.save()
            former_first_post = former_topic.first_post()
            text = former_first_post.text
            text = text.replace(current.get_absolute_url_beta(), exported.get_absolute_url_beta())
            former_first_post.update_content(text)
            former_first_post.save()
        # extract notes
        reacts = Note.objects.filter(tutorial__pk=current.pk)\
                             .select_related("author")\
                             .order_by("pubdate")\
                             .all()
        export_comments(reacts, exported, TutorialRead)
        if current.sha_public is not None and current.sha_public != "":
            publish_content(exported, exported.load_version(current.sha_public), False)
            exported.pubdate = current.pudate
            exported.sha_public = current.sha_public
            exported.save()
            exported.public_version.content_public_slug = current.slug
            exported.public_version.publication_date = current.pubdate
            exported.public_version.save()


def migrate_big_tuto():
    big_tutos = Tutorial.objects.prefetch_related("licence").filter(type="BIG").all()
    for i in progressbar(xrange(len(big_tutos)), "Exporting articles", 100):
        current = big_tutos[i]
        exported = PublishableContent()
        exported.slug = current.slug
        exported.type = "TUTORIAL"
        exported.title = current.title
        exported.sha_draft = current.sha_draft
        exported.licence = Licence.objects.filter(code=current.licence).first()
        exported.creation_date = current.create_at
        exported.image = current.image
        exported.description = current.description
        exported.js_support = current.js_support
        exported.save()
        [exported.subcategory.add(category) for category in current.subcategory.all()]
        [exported.helps.add(help) for help in current.helps.all()]
        [exported.authors.add(author) for author in current.authors.all()]
        shutil.copytree(current.get_path(False), exported.get_repo_path(False))
        # now, re create the manifest.json
        versioned = exported.load_version()
        versioned.licence = exported.licence
        exported.gallery = current.gallery
        versioned.type = "TUTORIAL"
        for part in Part.objects.filter(tutorial=current).all():
            current_part = Container(part.title, str(part.pk) + "_" + slugify(part.title))
            current_part.introduction = part.introduction
            current_part.conclusion = part.conclusion
            versioned.add_container(current_part)
            for chapter in Chapter.objects.filter(part=part).all():
                current_chapter = Container(chapter.title, str(chapter.pk) + "_" + slugify(chapter.title))
                current_chapter.introduction = chapter.introduction
                current_chapter.conclusion = chapter.conclusion
                current_part.add_container(current_chapter)
                for extract in OldExtract.objects.filter(chapter=chapter):
                    current_extract = Extract(extract.title, extract.text[:-3].split("/")[-1])
                    current_extract.text = extract.text
                    current_chapter.add_extract(current_extract)

        versioned.dump_json()

        exported.sha_draft = versioned.commit_changes(u"Migration version 2")
        exported.old_pk = current.pk
        if current.is_beta():
            exported.sha_beta = exported.sha_draft
            exported.beta_topic = Topic.objects.get(key=current.pk).first()

        exported.old_pk = current.pk
        exported.save()

        # export beta forum post
        former_topic = Topic.objet.get(key=current.pk)
        if former_topic is not None:
            former_topic.related_publishable_content = exported
            former_topic.save()
            former_first_post = former_topic.first_post()
            text = former_first_post.text
            text = text.replace(current.get_absolute_url_beta(), exported.get_absolute_url_beta())
            former_first_post.update_content(text)
            former_first_post.save()

        # todo: handle publication, notes etc.
    reacts = Note.objects.filter(tutorial__pk=current.pk)\
        .select_related("author")\
        .order_by("pubdate")\
        .all()
    export_comments(reacts, exported, TutorialRead)
    if current.sha_public is not None and current.sha_public != "":
        publish_content(exported, exported.load_version(current.sha_public), False)
        exported.pubdate = current.pudate
        exported.sha_public = current.sha_public
        exported.save()
        exported.public_version.content_public_slug = current.slug
        exported.public_version.publication_date = current.pubdate
        exported.public_version.save()


@transaction.atomic
class Command(BaseCommand):
    help = 'Migrate old tutorial and article stack to ZEP 12 stack (tutorialv2)'

    def handle(self, *args, **options):
        migrate_articles()
        migrate_mini_tuto()
        migrate_big_tuto()
