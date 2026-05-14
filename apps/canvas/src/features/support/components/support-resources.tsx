import { Link } from "react-router-dom";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import {
  BookOpen,
  Code2,
  Laptop,
  Linkedin,
  MessageCircle,
  MessageSquare,
  PlayCircle,
  Twitter,
  Users,
  Video,
} from "lucide-react";

interface ResourceLink {
  label: string;
  to: string;
  icon: React.ReactNode;
  badge?: string;
}

interface ResourceSection {
  title: string;
  description: string;
  cta: string;
  links: ResourceLink[];
}

const documentationLinks: ResourceLink[] = [
  {
    label: "Getting Started Guide",
    to: "#",
    icon: <BookOpen className="h-4 w-4" />,
  },
  { label: "API Reference", to: "#", icon: <Code2 className="h-4 w-4" /> },
  {
    label: "Workflow Examples",
    to: "#",
    icon: <Laptop className="h-4 w-4" />,
  },
  { label: "Node Reference", to: "#", icon: <Users className="h-4 w-4" /> },
];

const tutorialLinks: ResourceLink[] = [
  {
    label: "Introduction to Orcheo",
    to: "#",
    icon: <PlayCircle className="h-4 w-4" />,
    badge: "New",
  },
  {
    label: "Building Your First Workflow",
    to: "#",
    icon: <PlayCircle className="h-4 w-4" />,
  },
  {
    label: "Advanced Workflow Techniques",
    to: "#",
    icon: <PlayCircle className="h-4 w-4" />,
  },
  {
    label: "Debugging and Troubleshooting",
    to: "#",
    icon: <Video className="h-4 w-4" />,
  },
];

const communityLinks: ResourceLink[] = [
  {
    label: "Community Forum",
    to: "#",
    icon: <MessageSquare className="h-4 w-4" />,
  },
  {
    label: "Discord Server",
    to: "#",
    icon: <MessageCircle className="h-4 w-4" />,
  },
  {
    label: "LinkedIn Group",
    to: "#",
    icon: <Linkedin className="h-4 w-4" />,
  },
  {
    label: "Twitter",
    to: "#",
    icon: <Twitter className="h-4 w-4" />,
  },
];

const resourceSections: ResourceSection[] = [
  {
    title: "Documentation",
    description: "Explore our guides and examples",
    cta: "View All Documentation",
    links: documentationLinks,
  },
  {
    title: "Video Tutorials",
    description: "Learn through step-by-step videos",
    cta: "View All Tutorials",
    links: tutorialLinks,
  },
  {
    title: "Community",
    description: "Connect with other Orcheo users",
    cta: "Join Our Community",
    links: communityLinks,
  },
];

export function SupportResources() {
  return (
    <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {resourceSections.map((section) => (
        <Card key={section.title}>
          <CardHeader>
            <CardTitle>{section.title}</CardTitle>
            <CardDescription>{section.description}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2">
            {section.links.map((link) => (
              <Link
                key={link.label}
                to={link.to}
                className="flex items-center gap-2 rounded-md p-2 hover:bg-muted"
              >
                {link.icon}
                {link.label}
                {link.badge ? (
                  <Badge variant="secondary" className="ml-auto">
                    {link.badge}
                  </Badge>
                ) : null}
              </Link>
            ))}
          </CardContent>
          <CardFooter>
            <Button variant="outline" className="w-full">
              {section.cta}
            </Button>
          </CardFooter>
        </Card>
      ))}
    </section>
  );
}
